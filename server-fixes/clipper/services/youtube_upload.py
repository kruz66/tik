import logging

from clipper.services import zernio_youtube as zernio
from clipper.services.youtube import (
    CHANNEL_UPLOAD_LIMIT_MESSAGE,
    format_upload_error,
    get_credentials_for_project,
    is_channel_upload_limit_error,
    is_upload_quota_error,
    upload_video as google_upload,
)
from clipper.services.youtube_project_pool import (
    is_project_exhausted,
    load_projects,
    mark_project_exhausted,
    project_count,
)
from clipper.services.youtube_quota import handle_youtube_quota_hit
from clipper.services.zernio_youtube import get_connection

logger = logging.getLogger(__name__)

GOOGLE_QUOTA_MESSAGE = "YouTube daily API limit reached — try again tomorrow"


class YouTubeQuotaError(Exception):
    def __init__(
        self,
        message: str = GOOGLE_QUOTA_MESSAGE,
        *,
        is_channel_limit: bool = False,
    ):
        super().__init__(message)
        self.message = message
        self.is_channel_limit = is_channel_limit


def _credential_project_ids(connection) -> list[str]:
    creds_map = connection.project_credentials or {}
    if not isinstance(creds_map, dict):
        creds_map = {}
    ids = [pid for pid, raw in creds_map.items() if raw]
    if connection.active_project_id and connection.active_project_id not in ids:
        if connection.credentials_json:
            ids.append(connection.active_project_id)
    return ids


def projects_missing_credentials(user) -> list[str]:
    """Pool project IDs that still need a separate OAuth grant for this user."""
    connection = get_connection(user)
    creds_map = (connection.project_credentials or {}) if connection else {}
    if not isinstance(creds_map, dict):
        creds_map = {}
    missing = []
    for project in load_projects():
        pid = project["project_id"]
        if not creds_map.get(pid):
            missing.append(pid)
    return missing


def pool_credentials_linked(user) -> int:
    connection = get_connection(user)
    if not connection:
        return 0
    return len(_credential_project_ids(connection))


def pool_credentials_total() -> int:
    return project_count()


def user_has_youtube_upload_capacity(user) -> bool:
    """True when the user can attempt a YouTube upload right now."""
    if zernio.has_zernio_account(user) and zernio.is_configured():
        return True
    connection = get_connection(user)
    if not connection:
        return False
    for pid in _credential_project_ids(connection):
        if is_project_exhausted(pid):
            continue
        if get_credentials_for_project(user, pid):
            return True
    return bool(connection.credentials_json and connection.active_project_id)


def user_has_upload_path(user) -> bool:
    connection = get_connection(user)
    if not connection:
        return False
    if connection.zernio_account_id and zernio.is_configured():
        return True
    if _credential_project_ids(connection):
        return True
    return bool(connection.credentials_json)


def _upload_project_order(connection) -> list[str]:
    primary = connection.active_project_id or ""
    ordered = []
    for project in load_projects():
        pid = project["project_id"]
        if pid in _credential_project_ids(connection) and not is_project_exhausted(pid):
            ordered.append(pid)
    if primary and primary in ordered:
        ordered.sort(key=lambda pid: 0 if pid == primary else 1)
    return ordered


def upload_user_video(
    user,
    filepath: str,
    title: str,
    description: str = "",
    source: str = "tiktok",
) -> dict[str, str]:
    connection = get_connection(user)
    if not connection:
        raise RuntimeError("YouTube is not connected. Link your channel first.")

    zernio_ready = bool(connection.zernio_account_id and zernio.is_configured())
    project_ids = _upload_project_order(connection)
    creds_map = connection.project_credentials or {}
    if not isinstance(creds_map, dict):
        creds_map = {}

    if not project_ids and connection.credentials_json and connection.active_project_id:
        if not is_project_exhausted(connection.active_project_id):
            project_ids = [connection.active_project_id]

    last_quota_error = None
    tried = 0

    for project_id in project_ids:
        creds_json = creds_map.get(project_id)
        if not creds_json and connection.active_project_id == project_id:
            creds_json = connection.credentials_json
        if not creds_json:
            continue

        creds = get_credentials_for_project(user, project_id)
        if not creds:
            continue

        tried += 1
        try:
            result = google_upload(
                filepath=filepath,
                title=title,
                creds=creds,
                description=description,
                source=source,
            )
            connection.active_project_id = project_id
            connection.gcp_project_id = project_id
            connection.credentials_json = creds_json
            connection.save(
                update_fields=[
                    "active_project_id",
                    "gcp_project_id",
                    "credentials_json",
                    "updated_at",
                ]
            )
            logger.info(
                "Uploaded via Google project %s for %s", project_id, user.username
            )
            result["via_google"] = True
            result["project_id"] = project_id
            return result
        except Exception as exc:
            if is_channel_upload_limit_error(str(exc)):
                raise YouTubeQuotaError(
                    CHANNEL_UPLOAD_LIMIT_MESSAGE,
                    is_channel_limit=True,
                ) from exc
            if is_upload_quota_error(str(exc)):
                mark_project_exhausted(project_id)
                last_quota_error = exc
                logger.warning(
                    "Project %s quota exhausted for %s; trying next project",
                    project_id,
                    user.username,
                )
                continue
            raise

    if zernio_ready:
        result = zernio.upload_video(
            user,
            filepath,
            title,
            description=description,
            source=source,
        )
        logger.info("Uploaded via Zernio backup for %s", user.username)
        result["via_google"] = False
        return result

    if last_quota_error:
        if tried and not user_has_youtube_upload_capacity(user):
            handle_youtube_quota_hit(user)
        raise YouTubeQuotaError(format_upload_error(last_quota_error)) from last_quota_error

    if not load_projects():
        raise RuntimeError("YouTube OAuth project pool is not configured.")

    missing = projects_missing_credentials(user)
    if missing:
        linked = pool_credentials_linked(user)
        total = pool_credentials_total()
        raise RuntimeError(
            f"Only {linked}/{total} API projects are linked. "
            "Click Link YouTube again to authorize the remaining projects."
        )

    raise YouTubeQuotaError(
        "All linked API projects reached today's upload limit — try again tomorrow."
    )
