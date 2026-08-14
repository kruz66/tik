#!/usr/bin/env python3
"""Shorten channel-limit message and stop UI ellipsis truncation."""
from __future__ import annotations

import ast
import re
from pathlib import Path

YOUTUBE_PY = Path("/root/tiktok-cliper/clipper/services/youtube.py")
CSS = Path("/root/tiktok-cliper/static/clipper/css/panel.css")
PANEL_HTML = Path("/root/tiktok-cliper/templates/clipper/panel.html")


def patch_message() -> None:
    text = YOUTUBE_PY.read_text(encoding="utf-8")
    old_fn = '''def format_channel_upload_limit_message(resume_at=None) -> str:
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
    )'''
    new_fn = '''def format_channel_upload_limit_message(resume_at=None) -> str:
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
    )'''
    if old_fn not in text:
        if "Retry after" in text and "format_channel_upload_limit_message" in text:
            print("message already shortened")
        else:
            raise SystemExit("format_channel_upload_limit_message block not found")
    else:
        text = text.replace(old_fn, new_fn, 1)
        YOUTUBE_PY.write_text(text, encoding="utf-8")
        ast.parse(text)
        print("shortened channel limit message")


def patch_css() -> None:
    text = CSS.read_text(encoding="utf-8")

    old_progress = '''.progress-detail {
  width: 100%;
  line-height: 1.4;
  word-break: break-word;
  overflow: hidden;
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 3;
}'''
    new_progress = '''.progress-detail {
  width: 100%;
  line-height: 1.45;
  word-break: break-word;
  white-space: normal;
  overflow: visible;
  display: block;
  max-height: none;
}'''

    old_activity = '''.activity-stage-detail {
  margin: 0;
  font-size: 11px;
  line-height: 1.4;
  color: var(--text-muted);
  min-height: 0;
  flex-shrink: 0;
  word-break: break-word;
  overflow: hidden;
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 4;
  animation: activityDetailPulse 2.4s ease-in-out infinite;
}'''
    new_activity = '''.activity-stage-detail {
  margin: 0;
  font-size: 11px;
  line-height: 1.45;
  color: var(--text-muted);
  min-height: 0;
  flex-shrink: 0;
  word-break: break-word;
  white-space: normal;
  overflow: visible;
  display: block;
  max-height: none;
  animation: activityDetailPulse 2.4s ease-in-out infinite;
}'''

    changed = False
    if old_progress in text:
        text = text.replace(old_progress, new_progress, 1)
        changed = True
        print("patched .progress-detail")
    elif "progress-detail" in text and "-webkit-line-clamp: 3" not in text.split(".progress-detail")[1][:300]:
        print(".progress-detail already unclamped")
    else:
        raise SystemExit(".progress-detail block not found")

    if old_activity in text:
        text = text.replace(old_activity, new_activity, 1)
        changed = True
        print("patched .activity-stage-detail")
    elif "activity-stage-detail" in text and "-webkit-line-clamp: 4" not in text.split(".activity-stage-detail")[1][:400]:
        print(".activity-stage-detail already unclamped")
    else:
        raise SystemExit(".activity-stage-detail block not found")

    if changed:
        CSS.write_text(text, encoding="utf-8")


def bump_css_cache() -> None:
    if not PANEL_HTML.exists():
        print("panel.html missing; skip cache bump")
        return
    text = PANEL_HTML.read_text(encoding="utf-8")
    new_text, n = re.subn(
        r'(panel\.css\?v=)([^"\']+)',
        r"\g<1>20260814a",
        text,
        count=1,
    )
    if n:
        PANEL_HTML.write_text(new_text, encoding="utf-8")
        print("bumped panel.css cache buster to 20260814a")
    else:
        # try plain panel.css href
        new_text, n = re.subn(
            r'href="(/static/clipper/css/panel\.css)(?:\?v=[^"]*)?"',
            r'href="\1?v=20260814a"',
            text,
            count=1,
        )
        if n:
            PANEL_HTML.write_text(new_text, encoding="utf-8")
            print("set panel.css?v=20260814a")
        else:
            print("could not bump css cache; manual hard refresh may be needed")


if __name__ == "__main__":
    patch_message()
    patch_css()
    bump_css_cache()
