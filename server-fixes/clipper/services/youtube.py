import json
import logging
import os
from pathlib import Path

from django.conf import settings
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from clipper.models import YouTubeConnection
from clipper.services.thumbnail import extract_video_frame_thumbnail
from clipper.services.youtube_metadata import build_youtube_description, build_youtube_tags
from clipper.services.youtube_project_pool import (
    get_project,
    iter_projects_for_upload,
    load_projects,
    mark_project_exhausted,
)

logger = logging.getLogger(__name__)

SESSION_CREDS_KEY = "youtube_credentials"
YOUTUBE_COMMENT_SCOPE = "https://www.googleapis.com/auth/youtube.force-ssl"
YOUTUBE_ANALYTICS_SCOPE = "https://www.googleapis.com/auth/yt-analytics.readonly"


def oauth_scopes(include_analytics: bool = False) -> list[str]:
    scopes = list(settings.YOUTUBE_SCOPES)
    if include_analytics and YOUTUBE_ANALYTICS_SCOPE not in scopes:
        scopes.append(YOUTUBE_ANALYTICS_SCOPE)
    return scopes


def _credential_scopes(creds: Credentials | None = None, credentials_json: str = "") -> list[str]:
    if creds and creds.scopes:
        return list(creds.scopes)
    if credentials_json:
        try:
            info = json.loads(credentials_json)
            scopes = info.get("scopes") or []
            return scopes if isinstance(scopes, list) else []
        except (json.JSONDecodeError, TypeError):
            return []
    return []


def has_youtube_comment_scope(
    creds: Credentials | None = None,
    credentials_json: str = "",
) -> bool:
    return YOUTUBE_COMMENT_SCOPE in _credential_scopes(creds, credentials_json)


def has_youtube_analytics_scope(
    creds: Credentials | None = None,
    credentials_json: str = "",
) -> bool:
    return YOUTUBE_ANALYTICS_SCOPE in _credential_scopes(creds, credentials_json)


def build_redirect_uri(request=None) -> str:
    return settings.YOUTUBE_REDIRECT_URI


def pool_configured() -> bool:
    return bool(load_projects())


def _client_block(project_id: str) -> dict:
    project = get_project(project_id)
    if not project:
        raise ValueError(f"Unknown OAuth project: {project_id}")
    return project["client_config"]


def _refresh_if_needed(creds: Credentials) -> Credentials | None:
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError as exc:
            logger.warning("YouTube token refresh failed: %s", exc)
            return None
    return creds


def credentials_from_json(credentials_json: str, project_id: str = "") -> Credentials | None:
    if not credentials_json:
        return None
    try:
        info = json.loads(credentials_json)
        if project_id:
            block = (_client_block(project_id).get("web") or _client_block(project_id).get("installed") or {})
            if block.get("client_id"):
                info.setdefault("client_id", block["client_id"])
            if block.get("client_secret"):
                info.setdefault("client_secret", block["client_secret"])
        stored_scopes = info.get("scopes")
        scopes = (
            list(stored_scopes)
            if isinstance(stored_scopes, list) and stored_scopes
            else oauth_scopes()
        )
        creds = Credentials.from_authorized_user_info(info, scopes)
        creds = _refresh_if_needed(creds)
        return creds if creds and creds.valid else None
    except (json.JSONDecodeError, ValueError, TypeError, RefreshError):
        return None


def _get_connection(user) -> YouTubeConnection | None:
    if not user or not getattr(user, "is_authenticated", False):
        return None
    return YouTubeConnection.objects.filter(user=user).first()


def user_has_comment_scope(user) -> bool:
    connection = _get_connection(user)
    if not connection or not connection.credentials_json:
        return False
    return has_youtube_comment_scope(credentials_json=connection.credentials_json)


def user_has_analytics_scope(user) -> bool:
    connection = _get_connection(user)
    if not connection or not connection.credentials_json:
        return False
    return has_youtube_analytics_scope(credentials_json=connection.credentials_json)


def _project_credentials_map(connection: YouTubeConnection) -> dict:
    data = connection.project_credentials or {}
    return data if isinstance(data, dict) else {}


def save_project_credentials(
    user,
    session,
    project_id: str,
    creds: Credentials,
    channel: dict | None = None,
    mirror_to_pool: bool = False,
) -> YouTubeConnection:
    creds_json = creds.to_json()
    connection, _ = YouTubeConnection.objects.get_or_create(user=user)
    if mirror_to_pool and load_projects():
        connection.project_credentials = _mirror_credentials_to_pool(creds, project_id)
    else:
        creds_map = _project_credentials_map(connection)
        creds_map[project_id] = creds_json
        connection.project_credentials = creds_map
    connection.credentials_json = creds_json
    connection.active_project_id = project_id
    connection.gcp_project_id = project_id
    if channel:
        connection.channel_id = channel.get("id") or connection.channel_id
        connection.channel_title = channel.get("title") or connection.channel_title
        connection.channel_thumbnail = channel.get("thumbnail") or connection.channel_thumbnail
    connection.save()
    session[SESSION_CREDS_KEY] = creds_json
    session.modified = True
    return connection


def _mirror_credentials_to_pool(source_creds: Credentials, source_project_id: str) -> dict:
    """Copy one OAuth grant into every pool project using each project's client id."""
    base_info = json.loads(source_creds.to_json())
    creds_map: dict[str, str] = {}
    for project in load_projects():
        project_id = project["project_id"]
        block = project["client_config"].get("web") or project["client_config"].get("installed") or {}
        info = dict(base_info)
        if block.get("client_id"):
            info["client_id"] = block["client_id"]
        if block.get("client_secret"):
            info["client_secret"] = block["client_secret"]
        if block.get("token_uri"):
            info["token_uri"] = block["token_uri"]
        creds_map[project_id] = json.dumps(info)
    if source_project_id not in creds_map:
        creds_map[source_project_id] = source_creds.to_json()
    return creds_map


def _sync_credentials(user, session, creds: Credentials, project_id: str = "") -> None:
    creds_json = creds.to_json()
    session[SESSION_CREDS_KEY] = creds_json
    session.modified = True

    if not user or not getattr(user, "is_authenticated", False):
        return

    connection = _get_connection(user)
    project_id = project_id or (connection.active_project_id if connection else "")
    try:
        channel = get_channel_info(creds) or {}
    except Exception:
        channel = {}
    save_project_credentials(user, session, project_id, creds, channel=channel)


def get_credentials(user, session, project_id: str = "") -> Credentials | None:
    connection = _get_connection(user)

    def _load_from_json(creds_json: str, resolved_project: str) -> Credentials | None:
        if not creds_json:
            return None
        creds = credentials_from_json(creds_json, project_id=resolved_project)
        if not creds:
            return None
        if creds.expired and creds.refresh_token:
            creds = _refresh_if_needed(creds)
            if creds and creds.valid and connection and resolved_project:
                save_project_credentials(user, session, resolved_project, creds)
            elif not creds or not creds.valid:
                return None
        return creds if creds.valid else None

    resolved_project = project_id or (connection.active_project_id if connection else "")

    # 1) Try session cache first.
    session_json = session.get(SESSION_CREDS_KEY) or ""
    creds = _load_from_json(session_json, resolved_project)
    if creds:
        return creds

    # 2) Fall back to DB-stored credentials when session tokens are stale/bad.
    #    (Mobile browsers often keep an old session blob that fails refresh.)
    if connection:
        db_json = ""
        if project_id:
            db_json = _project_credentials_map(connection).get(project_id, "")
        if not db_json:
            db_json = connection.credentials_json or ""
        if not db_json and not project_id:
            # Any pool project token is enough to prove the account is linked.
            pool_map = _project_credentials_map(connection)
            if pool_map:
                first_pid = next(iter(pool_map))
                db_json = pool_map.get(first_pid, "")
                resolved_project = resolved_project or first_pid
        creds = _load_from_json(db_json, resolved_project)
        if creds:
            session[SESSION_CREDS_KEY] = db_json
            session.modified = True
            return creds
        # Clear a poisoned session cache so the next request retries from DB.
        if session_json and SESSION_CREDS_KEY in session:
            session.pop(SESSION_CREDS_KEY, None)
            session.modified = True

    return None


def mirror_user_credentials_to_pool(user) -> None:
    connection = _get_connection(user)
    if not connection or not connection.credentials_json:
        return
    project_id = connection.active_project_id or connection.gcp_project_id
    if not project_id and load_projects():
        project_id = load_projects()[0]["project_id"]
    if not project_id:
        return
    creds = credentials_from_json(connection.credentials_json, project_id=project_id)
    if not creds:
        return
    connection.project_credentials = _mirror_credentials_to_pool(creds, project_id)
    connection.save(update_fields=["project_credentials", "updated_at"])


def get_credentials_for_project(user, project_id: str) -> Credentials | None:
    """Return OAuth credentials stored for this pool project only.

    Tokens are not shared across pool clients; each project uses its own OAuth grant
    and API quota.
    """
    connection = _get_connection(user)
    if not connection:
        return None

    creds_map = _project_credentials_map(connection)
    creds_json = creds_map.get(project_id) or ""
    if not creds_json and connection.active_project_id == project_id:
        creds_json = connection.credentials_json
    if not creds_json:
        return None

    creds = credentials_from_json(creds_json, project_id=project_id)
    if not creds:
        return None

    stored = json.loads(creds_json)
    expected = (_client_block(project_id).get("web") or _client_block(project_id).get("installed") or {})
    if expected.get("client_id") and stored.get("client_id") != expected.get("client_id"):
        return None

    if creds.expired and creds.refresh_token:
        creds = _refresh_if_needed(creds)
    return creds if creds and creds.valid else None


def save_credentials(user, session, creds: Credentials, project_id: str = "") -> None:
    connection = _get_connection(user)
    resolved = project_id or (connection.active_project_id if connection else "")
    if not resolved and load_projects():
        resolved = load_projects()[0]["project_id"]
    _sync_credentials(user, session, creds, project_id=resolved)


def clear_credentials(user, session) -> None:
    session.pop(SESSION_CREDS_KEY, None)
    session.modified = True
    if user and user.is_authenticated:
        YouTubeConnection.objects.filter(user=user).delete()


def is_youtube_connected(user, session) -> bool:
    from clipper.services import zernio_youtube as zernio

    if zernio.has_zernio_account(user):
        return True
    connection = _get_connection(user)
    if not connection:
        return False
    # Prefer a live credential check, but if tokens are temporarily stale/
    # unreadable from the browser session, still treat the account as linked
    # so the sidebar shows Unlink YouTube (not Link) on mobile + desktop.
    if get_credentials(user, session) is not None:
        return True
    if connection.credentials_json:
        return True
    return bool(_project_credentials_map(connection))


def get_auth_flow(
    project_id: str,
    redirect_uri: str,
    state: str | None = None,
    code_verifier: str | None = None,
    *,
    include_analytics: bool = False,
) -> Flow:
    flow = Flow.from_client_config(
        _client_block(project_id),
        scopes=oauth_scopes(include_analytics=include_analytics),
        redirect_uri=redirect_uri,
    )
    if state:
        flow.oauth2session.state = state
    if code_verifier:
        flow.code_verifier = code_verifier
    return flow


def get_authorization_url(
    project_id: str,
    redirect_uri: str,
    *,
    include_analytics: bool = False,
    include_granted_scopes: bool = True,
) -> tuple[str, str, str]:
    flow = get_auth_flow(project_id, redirect_uri, include_analytics=include_analytics)
    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true" if include_granted_scopes else "false",
        prompt="consent",
    )
    return auth_url, state, flow.code_verifier


def exchange_code_for_credentials(
    project_id: str,
    code: str,
    redirect_uri: str,
    state: str | None = None,
    code_verifier: str | None = None,
    *,
    include_analytics: bool = False,
) -> Credentials:
    if not code_verifier:
        raise ValueError("Missing OAuth code verifier. Please try linking again.")

    flow = get_auth_flow(
        project_id,
        redirect_uri,
        state=state,
        code_verifier=code_verifier,
        include_analytics=include_analytics,
    )
    flow.fetch_token(code=code)
    return flow.credentials


def get_channel_info(creds: Credentials) -> dict | None:
    try:
        youtube = build("youtube", "v3", credentials=creds)
        response = youtube.channels().list(part="snippet,statistics", mine=True).execute()
    except HttpError as exc:
        logger.warning("Could not fetch YouTube channel info: %s", exc)
        return None

    items = response.get("items", [])
    if not items:
        return None

    channel = items[0]
    snippet = channel.get("snippet", {})
    stats = channel.get("statistics", {})
    return {
        "id": channel.get("id"),
        "title": snippet.get("title"),
        "thumbnail": snippet.get("thumbnails", {}).get("default", {}).get("url"),
        "subscriber_count": stats.get("subscriberCount"),
        "video_count": stats.get("videoCount"),
    }


def upload_video(
    filepath: str,
    title: str,
    creds: Credentials,
    description: str = "",
    tags: list[str] | None = None,
    source: str = "tiktok",
) -> dict[str, str]:
    if not creds or not creds.valid:
        raise RuntimeError("YouTube is not connected. Link your channel first.")

    if not os.path.exists(filepath):
        raise FileNotFoundError(f"Video file not found: {filepath}")

    youtube = build("youtube", "v3", credentials=creds)
    upload_description = build_youtube_description(description, title=title, source=source)
    upload_tags = build_youtube_tags(tags)

    body = {
        "snippet": {
            "title": title[:100],
            "description": upload_description,
            "tags": upload_tags,
            "categoryId": "22",
        },
        "status": {
            "privacyStatus": settings.YOUTUBE_PRIVACY,
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(filepath, mimetype="video/mp4", resumable=True)
    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
    )

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            logger.info("Upload progress: %d%%", int(status.progress() * 100))

    video_id = response.get("id", "")
    _set_video_thumbnail(youtube, video_id, filepath)

    return {
        "video_id": video_id,
        "url": f"https://www.youtube.com/watch?v={video_id}",
    }


def _set_video_thumbnail(youtube, video_id: str, video_filepath: str) -> None:
    if not video_id:
        return

    thumb_path = str(
        Path(video_filepath).with_name(f"{Path(video_filepath).stem}_thumb.jpg")
    )
    try:
        extract_video_frame_thumbnail(video_filepath, thumb_path)
        youtube.thumbnails().set(
            videoId=video_id,
            media_body=MediaFileUpload(thumb_path, mimetype="image/jpeg", resumable=True),
        ).execute()
        logger.info("Set video frame thumbnail for %s", video_id)
    except HttpError as exc:
        logger.warning("Could not set YouTube thumbnail for %s: %s", video_id, exc)
    except Exception as exc:
        logger.warning("Thumbnail extraction failed for %s: %s", video_id, exc)
    finally:
        try:
            if os.path.exists(thumb_path):
                os.remove(thumb_path)
        except OSError:
            pass


def is_channel_upload_limit_error(message: str) -> bool:
    lowered = (message or "").lower()
    return (
        "uploadlimitexceeded" in lowered
        or "exceeded the number of videos" in lowered
        or "exceeded the number of uploads" in lowered
    )


def is_upload_quota_error(message: str) -> bool:
    lowered = (message or "").lower()
    if is_channel_upload_limit_error(lowered):
        return False
    return (
        "quota exceeded" in lowered
        or "quotaexceeded" in lowered
        or "ratelimitexceeded" in lowered
    )


GOOGLE_QUOTA_MESSAGE = "YouTube daily API limit reached — try again tomorrow"
CHANNEL_UPLOAD_LIMIT_MESSAGE = (
    "YouTube channel daily upload limit reached for this Google account. "
    "Unverified OAuth apps can only upload a few videos per day until Google approves verification."
)


def format_channel_upload_limit_message(resume_at=None) -> str:
    """User-facing channel daily limit message including when uploads can resume.

    Keep this short so it fits the Progress panel without ellipsis truncation.
    """
    from clipper.services.youtube_quota import next_quota_reset_at
    from zoneinfo import ZoneInfo

    target = resume_at or next_quota_reset_at()
    pacific = target.astimezone(ZoneInfo("America/Los_Angeles"))
    when = pacific.strftime("%Y-%m-%d %H:%M %Z")
    return (
        f"YouTube daily upload limit reached (unverified app). "
        f"Retry after {when}."
    )


def format_upload_error(exc: Exception) -> str:
    text = str(exc).strip()
    if is_channel_upload_limit_error(text):
        return format_channel_upload_limit_message()
    if is_upload_quota_error(text):
        return GOOGLE_QUOTA_MESSAGE
    if text.startswith("<HttpError"):
        return "YouTube upload failed. Please try again."
    return text or "YouTube upload failed."


def _parse_iso_duration(duration: str) -> int:
    import re

    if not duration:
        return 0
    match = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration)
    if not match:
        return 0
    hours, minutes, seconds = match.groups()
    return int(hours or 0) * 3600 + int(minutes or 0) * 60 + int(seconds or 0)


def fetch_owned_channel_videos(creds: Credentials, max_results: int = 50) -> list[dict]:
    youtube = build("youtube", "v3", credentials=creds)
    channel_response = youtube.channels().list(part="contentDetails", mine=True).execute()
    items = channel_response.get("items") or []
    if not items:
        return []

    uploads_playlist = items[0].get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
    if not uploads_playlist:
        return []

    video_ids: list[str] = []
    next_page = None
    while len(video_ids) < max_results:
        playlist_response = (
            youtube.playlistItems()
            .list(
                part="contentDetails",
                playlistId=uploads_playlist,
                maxResults=min(50, max_results - len(video_ids)),
                pageToken=next_page,
            )
            .execute()
        )
        for item in playlist_response.get("items", []):
            video_id = item.get("contentDetails", {}).get("videoId")
            if video_id:
                video_ids.append(video_id)
        next_page = playlist_response.get("nextPageToken")
        if not next_page:
            break

    if not video_ids:
        return []

    videos_response = (
        youtube.videos()
        .list(part="snippet,statistics,contentDetails", id=",".join(video_ids))
        .execute()
    )

    results: list[dict] = []
    for video in videos_response.get("items", []):
        snippet = video.get("snippet", {})
        stats = video.get("statistics", {})
        content = video.get("contentDetails", {})
        duration = content.get("duration", "")
        duration_seconds = _parse_iso_duration(duration)
        results.append(
            {
                "id": video.get("id"),
                "title": snippet.get("title", ""),
                "url": f"https://www.youtube.com/watch?v={video.get('id')}",
                "published_at": snippet.get("publishedAt", ""),
                "thumbnail": snippet.get("thumbnails", {}).get("medium", {}).get("url", ""),
                "view_count": int(stats.get("viewCount") or 0),
                "like_count": int(stats.get("likeCount") or 0),
                "comment_count": int(stats.get("commentCount") or 0),
                "duration_seconds": duration_seconds,
                "is_short": 0 < duration_seconds <= 60,
            }
        )

    results.sort(key=lambda item: item.get("published_at", ""), reverse=True)
    return results


def fetch_channel_analytics(creds: Credentials, channel_id: str, days: int = 28) -> dict | None:
    if not channel_id or not has_youtube_analytics_scope(creds):
        return None

    from datetime import date, timedelta

    end_date = date.today()
    start_date = end_date - timedelta(days=days)
    start = start_date.isoformat()
    end = end_date.isoformat()
    channel_filter = f"channel=={channel_id}"

    try:
        analytics = build("youtubeAnalytics", "v2", credentials=creds)
    except Exception as exc:
        logger.warning("Could not build YouTube Analytics client: %s", exc)
        return None

    summary: dict = {
        "period_days": days,
        "views_by_day": [],
        "views_by_hour": [],
        "viewer_activity": [],
        "traffic_sources": [],
        "related_videos": [],
        "content_formats": [],
        "subscribed_status": [],
        "audience_channels": [],
    }

    try:
        daily = (
            analytics.reports()
            .query(
                ids=channel_filter,
                startDate=start,
                endDate=end,
                metrics="views,estimatedMinutesWatched,averageViewDuration",
                dimensions="day",
                sort="day",
            )
            .execute()
        )
        for row in daily.get("rows", []):
            summary["views_by_day"].append(
                {
                    "day": row[0],
                    "views": int(row[1] or 0),
                    "watch_minutes": float(row[2] or 0),
                    "avg_view_duration": float(row[3] or 0),
                }
            )
    except HttpError as exc:
        logger.warning("YouTube Analytics daily report failed: %s", exc)

    try:
        hourly = (
            analytics.reports()
            .query(
                ids=channel_filter,
                startDate=start,
                endDate=end,
                metrics="views",
                dimensions="day,hour",
                sort="day,hour",
            )
            .execute()
        )
        hour_totals: dict[int, dict] = {}
        activity_totals: dict[tuple[int, int], int] = {}
        for row in hourly.get("rows", []):
            if len(row) >= 3:
                day_index = int(row[0])
                hour = int(row[1])
                views = int(row[2] or 0)
                activity_totals[(day_index, hour)] = activity_totals.get((day_index, hour), 0) + views
            else:
                day_index = 0
                hour = int(row[0])
                views = int(row[1] or 0)
            bucket = hour_totals.setdefault(hour, {"hour": hour, "views": 0, "samples": 0})
            bucket["views"] += views
            bucket["samples"] += 1
        summary["views_by_hour"] = sorted(hour_totals.values(), key=lambda item: item["hour"])
        weekday_names = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
        summary["viewer_activity"] = [
            {
                "weekday": weekday_names[day % 7],
                "weekday_index": day,
                "hour": hour,
                "views": views,
            }
            for (day, hour), views in sorted(activity_totals.items())
        ]
    except HttpError as exc:
        logger.warning("YouTube Analytics hourly report failed: %s", exc)

    try:
        traffic = (
            analytics.reports()
            .query(
                ids=channel_filter,
                startDate=start,
                endDate=end,
                metrics="views",
                dimensions="insightTrafficSourceType",
                sort="-views",
                maxResults=10,
            )
            .execute()
        )
        for row in traffic.get("rows", []):
            summary["traffic_sources"].append({"source": row[0], "views": int(row[1] or 0)})
    except HttpError as exc:
        logger.warning("YouTube Analytics traffic report failed: %s", exc)

    try:
        related = (
            analytics.reports()
            .query(
                ids=channel_filter,
                startDate=start,
                endDate=end,
                metrics="views",
                dimensions="insightTrafficSourceDetail",
                filters="insightTrafficSourceType==RELATED_VIDEO",
                sort="-views",
                maxResults=15,
            )
            .execute()
        )
        for row in related.get("rows", []):
            summary["related_videos"].append({"video_id": row[0], "views": int(row[1] or 0)})
    except HttpError as exc:
        logger.warning("YouTube Analytics related-video report failed: %s", exc)

    try:
        formats = (
            analytics.reports()
            .query(
                ids=channel_filter,
                startDate=start,
                endDate=end,
                metrics="views",
                dimensions="creatorContentType",
                sort="-views",
            )
            .execute()
        )
        for row in formats.get("rows", []):
            summary["content_formats"].append({"format": row[0], "views": int(row[1] or 0)})
    except HttpError as exc:
        logger.warning("YouTube Analytics content-format report failed: %s", exc)

    try:
        subscribed = (
            analytics.reports()
            .query(
                ids=channel_filter,
                startDate=start,
                endDate=end,
                metrics="views",
                dimensions="subscribedStatus",
                sort="-views",
            )
            .execute()
        )
        total_sub_views = sum(int(row[1] or 0) for row in subscribed.get("rows", []))
        for row in subscribed.get("rows", []):
            views = int(row[1] or 0)
            summary["subscribed_status"].append(
                {
                    "status": row[0],
                    "views": views,
                    "percent": round((views / total_sub_views) * 100, 1) if total_sub_views else 0,
                }
            )
    except HttpError as exc:
        logger.warning("YouTube Analytics subscribed-status report failed: %s", exc)

    try:
        from clipper.services.youtube_audience import enrich_related_videos, resolve_audience_channels

        summary["related_videos"] = enrich_related_videos(creds, summary["related_videos"])
        summary["audience_channels"] = resolve_audience_channels(creds, summary["related_videos"])
    except Exception as exc:
        logger.warning("Audience channel enrichment failed: %s", exc)

    return summary
