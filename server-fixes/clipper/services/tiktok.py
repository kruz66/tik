import logging
import os
import re
import json
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse, parse_qs

import requests
import yt_dlp
from yt_dlp.networking.impersonate import ImpersonateTarget

from django.conf import settings

from .tiktok_utils import (
    extract_username,
    is_tiktok_video_url,
    normalize_tiktok_input,
    normalize_tiktok_video_url,
)

logger = logging.getLogger(__name__)

NO_WATERMARK_FORMAT = (
    "best[vcodec!=none][format_id!=download][format_note!*=watermarked][ext=mp4]/"
    "best[vcodec!=none][format_id!=download][ext=mp4]/"
    "best[vcodec!=none][format_note!*=watermarked]/"
    "best[vcodec!=none]/bestvideo+bestaudio/best"
)

VIDEO_EXTS = {".mp4", ".webm", ".mkv", ".mov"}
AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".wav", ".opus"}
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
# TikTok blocks plain yt-dlp TLS fingerprints; Chrome impersonation via curl_cffi
# is required for reliable profile listing and video downloads.
_YDL_IMPERSONATE = ImpersonateTarget.from_str("chrome")


def _base_ydl_opts() -> dict[str, Any]:
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "retries": 3,
        "fragment_retries": 3,
        "concurrent_fragment_downloads": 4,
        "impersonate": _YDL_IMPERSONATE,
    }
    device_id = getattr(settings, "TIKTOK_DEVICE_ID", "") or os.environ.get("TIKTOK_DEVICE_ID", "")
    if device_id:
        opts["extractor_args"] = {"tiktok": {"device_id": device_id}}
    return opts


def _video_caption(info: dict) -> str:
    for key in ("description", "title", "alt_title", "fulltitle"):
        value = (info.get(key) or "").strip()
        if value:
            return value
    return ""


def _entry_to_video(entry: dict[str, Any]) -> dict[str, Any]:
    video_id = str(entry.get("id") or "")
    video_url = entry.get("webpage_url") or entry.get("url") or ""
    if not video_url or not str(video_url).startswith("http"):
        uploader = entry.get("uploader") or entry.get("channel") or extract_username(video_url)
        if video_id and uploader:
            video_url = f"https://www.tiktok.com/@{uploader}/video/{video_id}"

    caption = _video_caption(entry)
    username = (
        entry.get("uploader")
        or entry.get("channel")
        or extract_username(video_url)
        or "tiktok"
    )
    thumb = entry.get("thumbnail") or ""
    if not thumb and entry.get("thumbnails"):
        thumb = entry.get("thumbnails", [{}])[0].get("url", "")

    return {
        "id": video_id,
        "source": "tiktok",
        "title": caption or f"TikTok video {video_id}",
        "description": caption,
        "url": video_url,
        "thumbnail": thumb,
        "duration": entry.get("duration") or 0,
        "view_count": entry.get("view_count") or 0,
        "upload_date": entry.get("upload_date") or "",
        "user_name": username,
        "user_login": str(username).lstrip("@"),
    }


_TIKTOK_VIDEO_URL_RE = re.compile(
    r"https?://(?:www\.)?tiktok\.com/@([\w.\-]+)/video/(\d+)",
    re.I,
)
_SEC_UID_RE = re.compile(r"^MS4wLjABAAAA[\w-]{64}$")
_SEC_UID_IN_TEXT_RE = re.compile(r"MS4wLjABAAAA[\w-]{64}")
_TIKTOK_AWEME_ID_RE = re.compile(r"^\d{15,20}$")
_ALIAS_CACHE_PREFIX = "tiktok_alias_identity:v1:"
_ALIAS_CACHE_SECONDS = 7 * 24 * 60 * 60


def _alias_cache_get(username: str) -> dict[str, str] | None:
    handle = (username or "").strip().lstrip("@").lower()
    if not handle:
        return None
    try:
        from django.core.cache import cache

        value = cache.get(f"{_ALIAS_CACHE_PREFIX}{handle}")
        if isinstance(value, dict) and (value.get("channel_id") or value.get("unique_id")):
            return {str(k): str(v) for k, v in value.items()}
    except Exception:
        logger.debug("TikTok alias cache read failed", exc_info=True)
    return None


def _alias_cache_set(username: str, identity: dict[str, str]) -> None:
    handle = (username or "").strip().lstrip("@").lower()
    if not handle or not identity:
        return
    try:
        from django.core.cache import cache

        cache.set(f"{_ALIAS_CACHE_PREFIX}{handle}", dict(identity), _ALIAS_CACHE_SECONDS)
    except Exception:
        logger.debug("TikTok alias cache write failed", exc_info=True)


def _remember_vanity_alias_from_video(video_url: str, info: dict[str, Any]) -> None:
    """If a video URL uses vanity @handle but API uploader differs, remember the mapping."""
    match = _TIKTOK_VIDEO_URL_RE.search(video_url or "")
    if not match or not info:
        return
    vanity = match.group(1).strip()
    unique_id = (
        (info.get("uploader") or "").strip().lstrip("@")
        or (info.get("channel") or "").strip().lstrip("@")
        or ""
    )
    channel_id = (info.get("channel_id") or "").strip()
    if not vanity or not (channel_id or unique_id):
        return
    if unique_id and unique_id.lower() == vanity.lower() and not channel_id:
        return
    identity = {
        "channel_id": channel_id,
        "unique_id": unique_id or vanity,
        "sample_video_url": (
            f"https://www.tiktok.com/@{vanity}/video/{match.group(2)}"
        ),
        "nickname": (info.get("channel") or info.get("uploader") or unique_id or vanity).strip(),
    }
    if unique_id.lower() != vanity.lower() or channel_id:
        _alias_cache_set(vanity, identity)


def fetch_single_tiktok_video(raw_input: str) -> dict[str, Any]:
    video_url = normalize_tiktok_video_url(raw_input)
    ydl_opts = {
        **_base_ydl_opts(),
        "skip_download": True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=False)
    except yt_dlp.utils.DownloadError as exc:
        logger.exception("TikTok video fetch failed")
        raise ValueError(f"Failed to fetch TikTok video: {exc}") from exc

    if not info:
        raise ValueError("No data returned for this TikTok video.")

    _remember_vanity_alias_from_video(video_url, info)

    video = _entry_to_video(info)
    if not video.get("id"):
        raise ValueError("Could not parse TikTok video ID.")

    username = video.get("user_login") or "tiktok"
    profile_title = info.get("uploader") or info.get("channel") or username
    profile_thumbnail = (
        info.get("uploader_avatar")
        or info.get("uploader_thumbnail")
        or info.get("channel_thumbnail")
        or video.get("thumbnail")
        or ""
    )

    return {
        "username": username,
        "profile_url": f"https://www.tiktok.com/@{username}",
        "profile_title": profile_title,
        "profile_thumbnail": profile_thumbnail,
        "videos": [video],
        "count": 1,
        "source": "tiktok",
    }


def _playlist_ydl_opts(limit: int | None = None) -> dict[str, Any]:
    ydl_opts = {
        **_base_ydl_opts(),
        "extract_flat": "in_playlist",
        "skip_download": True,
        "ignoreerrors": True,
    }
    if limit and limit > 0:
        ydl_opts["playlistend"] = limit
    return ydl_opts


def _videos_from_playlist_info(info: dict[str, Any] | None) -> list[dict[str, Any]]:
    videos: list[dict[str, Any]] = []
    if not info:
        return videos
    for entry in info.get("entries") or []:
        if not entry:
            continue
        video = _entry_to_video(entry)
        if not video.get("id"):
            continue
        videos.append(video)
    return videos


def _extract_user_playlist(profile_ref: str, limit: int | None = None) -> dict[str, Any] | None:
    """Fetch a TikTok user playlist. profile_ref may be a profile URL or tiktokuser:secUid."""
    try:
        with yt_dlp.YoutubeDL(_playlist_ydl_opts(limit)) as ydl:
            return ydl.extract_info(profile_ref, download=False)
    except yt_dlp.utils.DownloadError:
        logger.exception("TikTok playlist extract failed for %s", profile_ref)
        return None


def _collect_video_url(handle: str, candidate: str, seen: set[str], found: list[str]) -> None:
    match = _TIKTOK_VIDEO_URL_RE.search(candidate or "")
    if not match:
        return
    url_handle = match.group(1)
    video_id = match.group(2)
    if url_handle.lower() != handle.lower():
        return
    if not _TIKTOK_AWEME_ID_RE.match(video_id):
        return
    if video_id in seen:
        return
    seen.add(video_id)
    found.append(f"https://www.tiktok.com/@{url_handle}/video/{video_id}")


def _search_videos_duckduckgo(handle: str, *, max_urls: int) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    headers = {"User-Agent": _UA, "Accept-Language": "en-US,en;q=0.9"}
    queries = [
        f"site:tiktok.com/@{handle}/video",
        f'"tiktok.com/@{handle}/video"',
    ]
    for query in queries:
        if len(found) >= max_urls:
            break
        try:
            response = requests.get(
                "https://html.duckduckgo.com/html/",
                params={"q": query},
                headers=headers,
                timeout=8,
            )
            if response.status_code >= 400:
                continue
            html = response.text
            if "unfortunately, bots" in html.lower() or "anomaly-modal" in html.lower():
                logger.info("DuckDuckGo bot-check while resolving @%s", handle)
                break
        except Exception as exc:
            # Datacenter IPs often time out on DDG; keep this quiet so the
            # schedule watchdog is not blocked by stack traces / retries.
            logger.warning("DuckDuckGo lookup failed for TikTok alias %s: %s", handle, exc)
            continue

        for encoded in re.findall(r"uddg=([^&\"']+)", html):
            _collect_video_url(handle, unquote(encoded), seen, found)
        for match in _TIKTOK_VIDEO_URL_RE.finditer(html):
            _collect_video_url(handle, match.group(0), seen, found)
        for href in re.findall(r'href="([^"]+)"', html):
            if "uddg=" in href:
                parsed = urlparse(href if "://" in href else f"https://duckduckgo.com{href}")
                for value in parse_qs(parsed.query).get("uddg") or []:
                    _collect_video_url(handle, unquote(value), seen, found)
            elif "tiktok.com" in href:
                _collect_video_url(handle, unquote(href), seen, found)
    return found[:max_urls]


def _search_videos_wayback(handle: str, *, max_urls: int) -> list[str]:
    """Use Wayback CDX as a free index of historical @handle/video URLs."""
    found: list[str] = []
    seen: set[str] = set()
    # One CDX query is enough; a second mirror host often duplicates results.
    cdx_url = (
        "https://web.archive.org/cdx/search/cdx"
        f"?url=tiktok.com/@{handle}/video/*&output=json&fl=original&limit=40"
    )
    headers = {"User-Agent": _UA, "Accept": "application/json,text/plain,*/*"}
    try:
        response = requests.get(cdx_url, headers=headers, timeout=20)
        response.raise_for_status()
        rows = response.json()
    except Exception as exc:
        logger.warning("Wayback CDX lookup failed for TikTok alias %s: %s", handle, exc)
        return []
    if not isinstance(rows, list):
        return []
    for row in rows[1:]:
        if not row:
            continue
        original = row[0] if isinstance(row, list) else str(row)
        _collect_video_url(handle, original, seen, found)
        if len(found) >= max_urls:
            break
    return found[:max_urls]


def _search_videos_brightdata(handle: str, *, max_urls: int) -> list[str]:
    """Optional SERP via Bright Data when API credentials are configured."""
    api_key = (getattr(settings, "BRIGHTDATA_API_KEY", "") or "").strip()
    zone = (getattr(settings, "BRIGHTDATA_SERP_ZONE", "") or "").strip()
    if not api_key or not zone:
        return []

    from urllib.parse import quote_plus

    query = f"site:tiktok.com/@{handle}/video"
    search_url = f"https://www.google.com/search?q={quote_plus(query)}&num=10&brd_json=1"
    found: list[str] = []
    seen: set[str] = set()
    try:
        response = requests.post(
            "https://api.brightdata.com/request",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={"zone": zone, "url": search_url, "format": "raw"},
            timeout=45,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception:
        logger.exception("Bright Data SERP failed for TikTok alias %s", handle)
        return []

    organic = []
    if isinstance(payload, dict):
        organic = payload.get("organic") or payload.get("organic_results") or []
    if isinstance(organic, list):
        for item in organic:
            if not isinstance(item, dict):
                continue
            for key in ("link", "url", "display_link", "redirect_link"):
                _collect_video_url(handle, str(item.get(key) or ""), seen, found)
    for match in _TIKTOK_VIDEO_URL_RE.finditer(response.text):
        _collect_video_url(handle, match.group(0), seen, found)
    return found[:max_urls]


def _identity_from_profile_html(username: str) -> dict[str, str] | None:
    """
    Fast path: scrape the public profile HTML for secUid / uniqueId.
    Plain requests often still receive the page JSON even when yt-dlp's
    non-impersonated TLS fingerprint is blocked.
    """
    handle = (username or "").strip().lstrip("@")
    if not handle:
        return None
    url = f"https://www.tiktok.com/@{handle}"
    headers = {
        "User-Agent": _UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        response = requests.get(url, headers=headers, timeout=12)
        if response.status_code >= 400 or not response.text:
            return None
        html = response.text
    except Exception as exc:
        logger.warning("TikTok profile HTML fetch failed for @%s: %s", handle, exc)
        return None

    channel_ids = sorted(set(_SEC_UID_IN_TEXT_RE.findall(html)))
    # Prefer the first MS4w… match that appears near author metadata when possible.
    channel_id = channel_ids[0] if channel_ids else ""
    unique_match = re.search(
        rf'"uniqueId"\s*:\s*"({re.escape(handle)})"',
        html,
        flags=re.I,
    )
    unique_id = (unique_match.group(1) if unique_match else handle).strip().lstrip("@")
    nick_match = re.search(r'"nickname"\s*:\s*"([^"]{1,80})"', html)
    if not channel_id:
        # HTML loaded but no secUid — not enough to bypass a broken listing alone.
        return None
    identity = {
        "channel_id": channel_id,
        "unique_id": unique_id,
        "sample_video_url": "",
        "nickname": (nick_match.group(1) if nick_match else unique_id or handle).strip(),
    }
    logger.info(
        "Resolved TikTok @%s from profile HTML -> uniqueId=%s channel_id=%s",
        handle,
        unique_id,
        (channel_id or "")[:24],
    )
    return identity


def _search_tiktok_video_urls_for_username(username: str, *, max_urls: int = 8) -> list[str]:
    """Find public video URLs still published under @username (even if profile listing is broken)."""
    handle = (username or "").strip().lstrip("@")
    if not handle:
        return []

    # Prefer fast/reliable sources first. DuckDuckGo often times out from
    # datacenter IPs and previously stalled the schedule watchdog for minutes.
    for searcher in (
        _search_videos_wayback,
        _search_videos_brightdata,
        _search_videos_duckduckgo,
    ):
        try:
            found = searcher(handle, max_urls=max_urls)
        except Exception as exc:
            logger.warning(
                "Video URL searcher %s failed for @%s: %s",
                searcher.__name__,
                handle,
                exc,
            )
            continue
        if found:
            logger.info(
                "Discovered %s TikTok video URL(s) for @%s via %s",
                len(found),
                handle,
                searcher.__name__,
            )
            return found
    return []


def _identity_from_video_url(video_url: str) -> dict[str, str] | None:
    """Resolve channel_id / canonical uniqueId from a single public video."""
    try:
        with yt_dlp.YoutubeDL({**_base_ydl_opts(), "skip_download": True}) as ydl:
            info = ydl.extract_info(video_url, download=False)
    except yt_dlp.utils.DownloadError:
        logger.exception("Failed probing TikTok video for alias resolve: %s", video_url)
        return None
    if not info:
        return None

    _remember_vanity_alias_from_video(video_url, info)

    channel_id = (info.get("channel_id") or "").strip()
    unique_id = (
        (info.get("uploader") or "").strip().lstrip("@")
        or (info.get("channel") or "").strip().lstrip("@")
        or ""
    )
    if not channel_id and not unique_id:
        return None
    return {
        "channel_id": channel_id,
        "unique_id": unique_id,
        "sample_video_url": video_url,
        "nickname": (info.get("channel") or info.get("uploader") or unique_id or "").strip(),
    }


def discover_tiktok_account_identity(username: str) -> dict[str, str] | None:
    """
    When @username profile listing fails (embedding disabled / renamed handle),
    discover the API identity (secUid / canonical uniqueId) via public videos
    that still use the vanity @username in their URL.
    """
    handle = (username or "").strip().lstrip("@")
    if not handle:
        return None

    cached = _alias_cache_get(handle)
    if cached:
        logger.info("Using cached TikTok alias identity for @%s -> %s", handle, cached.get("unique_id"))
        return cached

    html_identity = _identity_from_profile_html(handle)
    if html_identity and html_identity.get("channel_id"):
        _alias_cache_set(handle, html_identity)
        return html_identity

    video_urls = _search_tiktok_video_urls_for_username(handle)
    if not video_urls:
        logger.info("No public videos discovered for TikTok alias @%s", handle)
        return None

    for video_url in video_urls[:4]:
        identity = _identity_from_video_url(video_url)
        if not identity:
            continue
        canonical = (identity.get("unique_id") or "").lower()
        if identity.get("channel_id") or (canonical and canonical != handle.lower()):
            logger.info(
                "Resolved TikTok @%s -> uniqueId=%s channel_id=%s via %s",
                handle,
                identity.get("unique_id"),
                (identity.get("channel_id") or "")[:24],
                video_url,
            )
            _alias_cache_set(handle, identity)
            return identity
    return None


def _fetch_playlist_via_identity(
    identity: dict[str, str],
    *,
    requested_username: str,
    limit: int | None = None,
) -> dict[str, Any] | None:
    channel_id = (identity.get("channel_id") or "").strip()
    unique_id = (identity.get("unique_id") or "").strip().lstrip("@")
    refs: list[str] = []
    if channel_id:
        refs.append(f"tiktokuser:{channel_id}")
    if unique_id and unique_id.lower() != requested_username.lower():
        refs.append(f"https://www.tiktok.com/@{unique_id}")

    for ref in refs:
        info = _extract_user_playlist(ref, limit)
        videos = _videos_from_playlist_info(info)
        if not videos:
            continue
        profile_title = (
            (info or {}).get("uploader")
            or (info or {}).get("channel")
            or (info or {}).get("title")
            or unique_id
            or requested_username
        )
        profile_thumbnail = (
            (info or {}).get("uploader_avatar")
            or (info or {}).get("uploader_thumbnail")
            or (info or {}).get("channel_thumbnail")
            or (info or {}).get("thumbnail")
            or ""
        )
        resolved_username = unique_id or requested_username
        profile_url = f"https://www.tiktok.com/@{resolved_username}"
        if limit and limit > 0:
            videos = videos[:limit]
        if not profile_thumbnail and videos:
            profile_thumbnail = videos[0].get("thumbnail") or ""
        return {
            "username": resolved_username,
            "requested_username": requested_username,
            "resolved_username": resolved_username,
            "alias_resolved": resolved_username.lower() != requested_username.lower(),
            "channel_id": channel_id,
            "profile_url": profile_url,
            "profile_title": profile_title,
            "profile_thumbnail": profile_thumbnail,
            "videos": videos,
            "count": len(videos),
            "source": "tiktok",
        }
    return None


def fetch_recent_videos(raw_input: str, limit: int | None = None) -> dict[str, Any]:
    if is_tiktok_video_url(raw_input):
        return fetch_single_tiktok_video(raw_input)

    profile_url = normalize_tiktok_input(raw_input)
    username = extract_username(profile_url)

    primary_error: Exception | None = None
    videos: list[dict[str, Any]] = []
    profile_title = username
    profile_thumbnail = ""

    try:
        info = _extract_user_playlist(profile_url, limit)
        if not info:
            primary_error = ValueError("No data returned for this TikTok account.")
        else:
            profile_title = info.get("uploader") or info.get("channel") or info.get("title") or username
            profile_thumbnail = (
                info.get("uploader_avatar")
                or info.get("uploader_thumbnail")
                or info.get("channel_thumbnail")
                or info.get("thumbnail")
                or ""
            )
            videos = _videos_from_playlist_info(info)
            if not videos:
                primary_error = ValueError(
                    "No videos found for this account. The profile may be private or unavailable."
                )
    except yt_dlp.utils.DownloadError as exc:
        logger.exception("TikTok fetch failed")
        primary_error = ValueError(f"Failed to fetch TikTok videos: {exc}")

    if videos:
        if limit and limit > 0:
            videos = videos[:limit]
        if not profile_thumbnail and videos:
            profile_thumbnail = videos[0].get("thumbnail") or ""
        return {
            "username": username,
            "profile_url": profile_url,
            "profile_title": profile_title,
            "profile_thumbnail": profile_thumbnail,
            "videos": videos,
            "count": len(videos),
            "source": "tiktok",
        }

    # Profile listing failed (common when vanity @handle has embedding disabled
    # or redirects to a different API uniqueId). Discover the real identity from
    # public videos that still use the requested @handle in their URL.
    logger.warning(
        "TikTok profile fetch failed for @%s (%s); attempting alias discovery",
        username,
        primary_error,
    )
    identity = discover_tiktok_account_identity(username)
    if identity:
        resolved = _fetch_playlist_via_identity(
            identity,
            requested_username=username,
            limit=limit,
        )
        if resolved:
            logger.info(
                "TikTok alias resolve succeeded: @%s -> @%s (%s videos)",
                username,
                resolved.get("resolved_username"),
                resolved.get("count"),
            )
            return resolved

    if primary_error:
        raise primary_error
    raise ValueError(
        "No videos found for this account. The profile may be private or unavailable."
    )


def download_video(
    video_url: str,
    output_dir: str,
    video_id: str,
    user=None,
) -> dict[str, str]:
    from clipper.services.tiktok_cookies import resolve_tiktok_cookies_path

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    output_template = str(out_dir / f"{video_id}.%(ext)s")

    ydl_opts = {
        **_base_ydl_opts(),
        "outtmpl": output_template,
        "format": NO_WATERMARK_FORMAT,
        "merge_output_format": "mp4",
    }
    cookies_file = resolve_tiktok_cookies_path(user)
    if cookies_file:
        ydl_opts["cookiefile"] = cookies_file

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(video_url, download=True)
        prepared = Path(ydl.prepare_filename(info))

    caption = _video_caption(info)
    title = caption[:100] if caption else f"TikTok clip {video_id}"
    meta = {
        "title": title,
        "description": caption,
        "id": video_id,
    }

    filepath = _resolve_downloaded_media(out_dir, video_id, prepared)
    if filepath and filepath.suffix.lower() in VIDEO_EXTS and filepath.is_file():
        return {"filepath": str(filepath), **meta}

    # Photo-mode / slideshow posts only expose audio via yt-dlp.
    audio_path = filepath if filepath and filepath.suffix.lower() in AUDIO_EXTS else None
    if not audio_path:
        audio_path = _find_media_file(out_dir, video_id, AUDIO_EXTS)

    try:
        slideshow = _build_photo_mode_mp4(
            video_url=video_url,
            output_dir=out_dir,
            video_id=video_id,
            audio_path=audio_path,
            duration_hint=float(info.get("duration") or 0) or None,
        )
        if slideshow and slideshow.is_file():
            return {"filepath": str(slideshow), **meta}
    except Exception:
        logger.exception("TikTok photo-mode rebuild failed for %s", video_id)

    if audio_path and audio_path.is_file():
        raise FileNotFoundError(
            f"TikTok returned audio only for {video_id} (photo post or restricted video). "
            "Could not build a video file from it."
        )
    raise FileNotFoundError(
        f"Download finished but video file is missing for {video_id}."
    )


def _find_media_file(directory: Path, video_id: str, exts: set[str]) -> Path | None:
    matches = [
        path
        for path in directory.glob(f"{video_id}.*")
        if path.is_file() and path.suffix.lower() in exts and path.stat().st_size > 0
    ]
    if not matches:
        return None
    matches.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return matches[0]


def _resolve_downloaded_media(directory: Path, video_id: str, prepared: Path) -> Path | None:
    """Prefer a real on-disk file over yt-dlp's guessed .mp4 path."""
    if prepared.is_file() and prepared.stat().st_size > 0:
        return prepared

    video = _find_media_file(directory, video_id, VIDEO_EXTS)
    if video:
        return video

    # yt-dlp may write stem.mp4 after merge while prepare_filename pointed elsewhere.
    guessed_mp4 = prepared.with_suffix(".mp4")
    if guessed_mp4.is_file() and guessed_mp4.stat().st_size > 0:
        return guessed_mp4

    return _find_media_file(directory, video_id, AUDIO_EXTS)


def _fetch_photo_mode_assets(video_url: str) -> tuple[list[str], str | None]:
    """Pull image URLs + music URL from TikTok photo-mode page data."""
    response = requests.get(
        video_url,
        headers={"User-Agent": _UA, "Referer": "https://www.tiktok.com/"},
        timeout=45,
    )
    response.raise_for_status()
    match = re.search(
        r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>',
        response.text,
    )
    if not match:
        return [], None

    data = json.loads(match.group(1))
    image_post, parent = _find_image_post(data)
    if not image_post:
        return [], None

    image_urls: list[str] = []
    for image in image_post.get("images") or []:
        url_list = ((image.get("imageURL") or {}).get("urlList")) or []
        if url_list:
            image_urls.append(str(url_list[0]))

    music = ""
    if isinstance(parent, dict):
        music_obj = parent.get("music") or {}
        if isinstance(music_obj, dict):
            music = str(music_obj.get("playUrl") or music_obj.get("play_url") or "")
    return image_urls, (music or None)


def _find_image_post(node: Any) -> tuple[dict | None, dict | None]:
    if isinstance(node, dict):
        image_post = node.get("imagePost")
        if isinstance(image_post, dict) and image_post.get("images"):
            return image_post, node
        for value in node.values():
            found = _find_image_post(value)
            if found[0] is not None:
                return found
    elif isinstance(node, list):
        for value in node:
            found = _find_image_post(value)
            if found[0] is not None:
                return found
    return None, None


def _download_binary(url: str, dest: Path) -> Path:
    response = requests.get(
        url,
        headers={"User-Agent": _UA, "Referer": "https://www.tiktok.com/"},
        timeout=90,
        stream=True,
    )
    response.raise_for_status()
    with dest.open("wb") as handle:
        for chunk in response.iter_content(chunk_size=1024 * 256):
            if chunk:
                handle.write(chunk)
    if not dest.is_file() or dest.stat().st_size <= 0:
        raise FileNotFoundError(f"Failed to download asset: {url[:80]}")
    return dest


def _probe_duration_seconds(path: Path) -> float:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        return float((result.stdout or "").strip() or 0)
    except Exception:
        return 0.0


def _build_photo_mode_mp4(
    *,
    video_url: str,
    output_dir: Path,
    video_id: str,
    audio_path: Path | None,
    duration_hint: float | None = None,
) -> Path | None:
    image_urls, music_url = _fetch_photo_mode_assets(video_url)
    if not image_urls:
        return None

    work = output_dir / f"{video_id}_photomode"
    work.mkdir(parents=True, exist_ok=True)

    image_files: list[Path] = []
    for index, image_url in enumerate(image_urls[:20]):
        ext = ".jpg"
        lower = image_url.lower()
        if ".png" in lower:
            ext = ".png"
        elif ".webp" in lower:
            ext = ".webp"
        dest = work / f"img_{index:02d}{ext}"
        try:
            _download_binary(image_url, dest)
            image_files.append(dest)
        except Exception:
            logger.warning("Skipping TikTok photo asset %s", image_url[:100])

    if not image_files:
        return None

    if audio_path is None or not audio_path.is_file():
        if not music_url:
            return None
        audio_path = work / f"{video_id}_music.mp3"
        _download_binary(music_url, audio_path)

    duration = _probe_duration_seconds(audio_path) or float(duration_hint or 0) or 0.0
    if duration <= 0:
        duration = max(3.0 * len(image_files), 6.0)
    per_image = max(duration / len(image_files), 1.5)

    # Build equal-length clips per image, then concat + mux audio.
    clip_paths: list[Path] = []
    for index, image in enumerate(image_files):
        clip = work / f"clip_{index:02d}.mp4"
        cmd = [
            "ffmpeg",
            "-y",
            "-loop",
            "1",
            "-t",
            f"{per_image:.3f}",
            "-i",
            str(image),
            "-vf",
            "scale=1080:1920:force_original_aspect_ratio=decrease,"
            "pad=1080:1920:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30,format=yuv420p",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-pix_fmt",
            "yuv420p",
            "-an",
            str(clip),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if result.returncode != 0 or not clip.is_file():
            logger.warning(
                "ffmpeg photo clip failed for %s: %s",
                image.name,
                (result.stderr or "")[-400:],
            )
            continue
        clip_paths.append(clip)

    if not clip_paths:
        return None

    concat_list = work / "concat.txt"
    concat_list.write_text(
        "".join(f"file '{path.resolve()}'\n" for path in clip_paths),
        encoding="utf-8",
    )
    silent_video = work / f"{video_id}_silent.mp4"
    concat_cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_list),
        "-c",
        "copy",
        str(silent_video),
    ]
    result = subprocess.run(concat_cmd, capture_output=True, text=True, timeout=180)
    if result.returncode != 0 or not silent_video.is_file():
        raise RuntimeError((result.stderr or "ffmpeg concat failed")[-500:])

    final_path = output_dir / f"{video_id}.mp4"
    mux_cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(silent_video),
        "-i",
        str(audio_path),
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-shortest",
        "-movflags",
        "+faststart",
        str(final_path),
    ]
    result = subprocess.run(mux_cmd, capture_output=True, text=True, timeout=180)
    if result.returncode != 0 or not final_path.is_file():
        raise RuntimeError((result.stderr or "ffmpeg mux failed")[-500:])
    return final_path
