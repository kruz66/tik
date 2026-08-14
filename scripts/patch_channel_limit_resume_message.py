#!/usr/bin/env python3
"""Update channel upload limit message to include resume date/time."""
from __future__ import annotations

import ast
import sys
from pathlib import Path

YOUTUBE_PY = Path("/root/tiktok-cliper/clipper/services/youtube.py")
UPLOAD_PY = Path("/root/tiktok-cliper/clipper/services/youtube_upload.py")


def patch_youtube_py() -> None:
    text = YOUTUBE_PY.read_text(encoding="utf-8")
    if "format_channel_upload_limit_message" in text:
        print("youtube.py already patched")
        return

    old = '''GOOGLE_QUOTA_MESSAGE = "YouTube daily API limit reached — try again tomorrow"
CHANNEL_UPLOAD_LIMIT_MESSAGE = (
    "YouTube channel daily upload limit reached for this Google account. "
    "Unverified OAuth apps can only upload a few videos per day until Google approves verification."
)


def format_upload_error(exc: Exception) -> str:
    text = str(exc).strip()
    if is_channel_upload_limit_error(text):
        return CHANNEL_UPLOAD_LIMIT_MESSAGE
    if is_upload_quota_error(text):
        return GOOGLE_QUOTA_MESSAGE
'''

    new = '''GOOGLE_QUOTA_MESSAGE = "YouTube daily API limit reached — try again tomorrow"
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
'''

    if old not in text:
        raise SystemExit("youtube.py target block not found")
    text = text.replace(old, new, 1)
    YOUTUBE_PY.write_text(text, encoding="utf-8")
    ast.parse(text)
    print("patched youtube.py")


def patch_upload_py() -> None:
    text = UPLOAD_PY.read_text(encoding="utf-8")
    if "format_channel_upload_limit_message" in text and "CHANNEL_UPLOAD_LIMIT_MESSAGE," not in text.split("raise YouTubeQuotaError")[0][-200:]:
        # still need to check raise site
        pass

    # Update import
    old_import = """from clipper.services.youtube import (
    CHANNEL_UPLOAD_LIMIT_MESSAGE,
    format_upload_error,
    get_credentials_for_project,
    is_channel_upload_limit_error,
    is_upload_quota_error,
    upload_video as google_upload,
)"""
    new_import = """from clipper.services.youtube import (
    format_channel_upload_limit_message,
    format_upload_error,
    get_credentials_for_project,
    is_channel_upload_limit_error,
    is_upload_quota_error,
    upload_video as google_upload,
)"""
    if old_import in text:
        text = text.replace(old_import, new_import, 1)
    elif "format_channel_upload_limit_message" not in text:
        text = text.replace(
            "CHANNEL_UPLOAD_LIMIT_MESSAGE,\n",
            "format_channel_upload_limit_message,\n",
            1,
        )

    old_raise = """            if is_channel_upload_limit_error(str(exc)):
                raise YouTubeQuotaError(
                    CHANNEL_UPLOAD_LIMIT_MESSAGE,
                    is_channel_limit=True,
                ) from exc"""
    new_raise = """            if is_channel_upload_limit_error(str(exc)):
                raise YouTubeQuotaError(
                    format_channel_upload_limit_message(),
                    is_channel_limit=True,
                ) from exc"""
    if old_raise not in text:
        if "format_channel_upload_limit_message()" in text:
            print("youtube_upload.py raise already patched")
        else:
            raise SystemExit("youtube_upload.py raise block not found")
    else:
        text = text.replace(old_raise, new_raise, 1)

    UPLOAD_PY.write_text(text, encoding="utf-8")
    ast.parse(text)
    print("patched youtube_upload.py")


def main() -> None:
    patch_youtube_py()
    patch_upload_py()
    # smoke
    sys.path.insert(0, "/root/tiktok-cliper")
    import os
    import django

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    django.setup()
    from clipper.services.youtube import format_channel_upload_limit_message

    msg = format_channel_upload_limit_message()
    print("SAMPLE:", msg)
    assert "after " in msg and ("PDT" in msg or "PST" in msg)


if __name__ == "__main__":
    main()
