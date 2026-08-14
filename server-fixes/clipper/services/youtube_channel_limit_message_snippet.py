        or "ratelimitexceeded" in lowered
    )


GOOGLE_QUOTA_MESSAGE = "YouTube daily API limit reached — try again tomorrow"
CHANNEL_UPLOAD_LIMIT_MESSAGE = (
    "YouTube channel daily upload limit reached for this Google account. "
    "Unverified OAuth apps can only upload a few videos per day until Google approves verification."
)


def format_channel_upload_limit_message(resume_at=None) -> str:
    """User-facing channel daily limit message including when uploads can resume."""
    from clipper.services.youtube_quota import next_quota_reset_at

    target = resume_at or next_quota_reset_at()
    # YouTube caps reset at midnight Pacific; show that timezone explicitly.
    from zoneinfo import ZoneInfo

    pacific = target.astimezone(ZoneInfo("America/Los_Angeles"))
    when = pacific.strftime("%Y-%m-%d %H:%M %Z")
    return (
        f"{CHANNEL_UPLOAD_LIMIT_MESSAGE} "
        f"You can try uploading again after {when}."
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

