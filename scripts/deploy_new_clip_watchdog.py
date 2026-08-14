#!/usr/bin/env python3
"""Deploy new-clip-only watchdog + stop re-posting already-seen/old clips."""
from __future__ import annotations

import ast
import subprocess
from pathlib import Path
from textwrap import dedent

ROOT = Path("/root/tiktok-cliper")
MODELS = ROOT / "clipper" / "models.py"
RUNNER = ROOT / "clipper" / "services" / "schedule_runner.py"
SETTINGS = ROOT / "config" / "settings.py"
TIMER = Path("/etc/systemd/system/tiktok-cliper-schedule.timer")


def patch_models() -> None:
    text = MODELS.read_text(encoding="utf-8")
    if "baseline_seeded_at" in text and "seen_video_ids" in text:
        print("models already patched")
        return
    needle = '''    last_checked_at = models.DateTimeField(null=True, blank=True)
    last_check_message = models.TextField(blank=True)
    uploads_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
'''
    replacement = '''    last_checked_at = models.DateTimeField(null=True, blank=True)
    last_check_message = models.TextField(blank=True)
    uploads_count = models.PositiveIntegerField(default=0)
    # Watchdog: after baseline seeding, only NEW source video IDs are uploaded.
    baseline_seeded_at = models.DateTimeField(null=True, blank=True)
    seen_video_ids = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
'''
    if needle not in text:
        raise SystemExit("ScheduledSource fields block not found")
    MODELS.write_text(text.replace(needle, replacement, 1), encoding="utf-8")
    ast.parse(MODELS.read_text(encoding="utf-8"))
    print("patched models.py")


def patch_settings() -> None:
    text = SETTINGS.read_text(encoding="utf-8")
    changed = False
    if 'SCHEDULE_CHECK_MINUTES = int(os.environ.get("SCHEDULE_CHECK_MINUTES", "2"))' in text:
        text = text.replace(
            'SCHEDULE_CHECK_MINUTES = int(os.environ.get("SCHEDULE_CHECK_MINUTES", "2"))',
            'SCHEDULE_CHECK_MINUTES = int(os.environ.get("SCHEDULE_CHECK_MINUTES", "1"))',
            1,
        )
        changed = True
    if "SCHEDULE_WATCH_MAX_AGE_HOURS" not in text:
        text = text.replace(
            'SCHEDULE_FETCH_LIMIT = int(os.environ.get("SCHEDULE_FETCH_LIMIT", "30"))',
            'SCHEDULE_FETCH_LIMIT = int(os.environ.get("SCHEDULE_FETCH_LIMIT", "20"))\n'
            'SCHEDULE_WATCH_MAX_AGE_HOURS = int(os.environ.get("SCHEDULE_WATCH_MAX_AGE_HOURS", "6"))\n'
            'SCHEDULE_SEEN_IDS_LIMIT = int(os.environ.get("SCHEDULE_SEEN_IDS_LIMIT", "800"))',
            1,
        )
        changed = True
    if changed:
        SETTINGS.write_text(text, encoding="utf-8")
        print("patched settings.py")
    else:
        print("settings already ok")


def write_watch_helpers_into_runner() -> None:
    """Replace key functions in schedule_runner.py with watchdog-aware versions."""
    text = RUNNER.read_text(encoding="utf-8")

    # Ensure settings import usage is fine - already imports settings

    old_known = '''def _known_video_ids_for_source(user, source: ScheduledSource) -> set[str]:
    uploaded = set(
        ScheduledUploadRecord.objects.filter(user=user, source=source).values_list(
            "source_video_id", flat=True
        )
    )
    queued = set(
        ScheduledUploadQueue.objects.filter(user=user, source=source)
        .exclude(status=ScheduledUploadQueue.Status.FAILED)
        .values_list("source_video_id", flat=True)
    )
    return uploaded | queued
'''

    new_known = '''def _known_video_ids_for_user(user) -> set[str]:
    """All source video IDs already uploaded, queued, or marked seen for this user."""
    uploaded = set(
        ScheduledUploadRecord.objects.filter(user=user).values_list(
            "source_video_id", flat=True
        )
    )
    queued = set(
        ScheduledUploadQueue.objects.filter(user=user)
        .exclude(status=ScheduledUploadQueue.Status.FAILED)
        .values_list("source_video_id", flat=True)
    )
    # Also treat Clip Panel uploads as already posted when tiktok_id matches.
    panel_ids = set(
        UploadedVideo.objects.filter(job__user=user)
        .exclude(tiktok_id="")
        .exclude(youtube_video_id="")
        .values_list("tiktok_id", flat=True)
    )
    seen_from_sources: set[str] = set()
    for raw in ScheduledSource.objects.filter(user=user).values_list("seen_video_ids", flat=True):
        if isinstance(raw, list):
            seen_from_sources.update(str(x) for x in raw if x)
    return uploaded | queued | {str(x) for x in panel_ids if x} | seen_from_sources


def _known_video_ids_for_source(user, source: ScheduledSource) -> set[str]:
    """Backward-compatible alias — prefer user-global known set for dedupe."""
    return _known_video_ids_for_user(user)


def _remember_seen_ids(source: ScheduledSource, video_ids: list[str]) -> None:
    """Persist discovered IDs so old clips are never treated as new again."""
    limit = int(getattr(settings, "SCHEDULE_SEEN_IDS_LIMIT", 800))
    current = source.seen_video_ids if isinstance(source.seen_video_ids, list) else []
    merged: list[str] = []
    seen: set[str] = set()
    # Newest discoveries first.
    for vid in list(video_ids) + [str(x) for x in current]:
        vid = str(vid or "").strip()
        if not vid or vid in seen:
            continue
        seen.add(vid)
        merged.append(vid)
        if len(merged) >= limit:
            break
    source.seen_video_ids = merged
    source.save(update_fields=["seen_video_ids", "updated_at"])


def _seed_source_baseline(source: ScheduledSource, videos: list[dict]) -> dict:
    """Mark everything currently listed as seen WITHOUT uploading.

    This stops the schedule from dumping days-old clips when a source is added
    or when the watchdog is first enabled.
    """
    ids = [str(v.get("id") or "").strip() for v in videos if v.get("id")]
    ids = [vid for vid in ids if vid]
    _remember_seen_ids(source, ids)
    source.baseline_seeded_at = timezone.now()
    source.last_checked_at = timezone.now()
    source.last_check_message = (
        f"Watching for NEW clips only — baseline set with {len(ids)} existing video(s). "
        "Older posts will not be uploaded."
    )
    source.save(
        update_fields=[
            "baseline_seeded_at",
            "last_checked_at",
            "last_check_message",
            "updated_at",
        ]
    )
    # Drop pending/failed queue rows for this source that are already in the baseline.
    if ids:
        ScheduledUploadQueue.objects.filter(
            user=source.user,
            source=source,
            source_video_id__in=ids,
            status__in=[
                ScheduledUploadQueue.Status.PENDING,
                ScheduledUploadQueue.Status.FAILED,
            ],
        ).delete()
    return {
        "uploaded": 0,
        "queued": 0,
        "message": source.last_check_message,
        "new_found": 0,
        "baseline_seeded": True,
    }
'''

    if "_seed_source_baseline" in text and "baseline_seeded_at" in text:
        print("runner helpers already present")
    else:
        if old_known not in text:
            raise SystemExit("_known_video_ids_for_source block not found")
        if "UploadedVideo" not in text:
            text = text.replace(
                "from clipper.models import ScheduledSource, ScheduledUploadQueue, ScheduledUploadRecord",
                "from clipper.models import ScheduledSource, ScheduledUploadQueue, ScheduledUploadRecord, UploadedVideo",
                1,
            )
        text = text.replace(old_known, new_known, 1)
        print("patched known-id helpers")

    # Patch check_scheduled_source discovery section
    old_check_mid = '''    try:
        data = fetch_videos(
            source.input_value,
            limit=settings.SCHEDULE_FETCH_LIMIT,
            source=source.source_type,
        )
    except ValueError as exc:
        source.last_check_message = str(exc)
        source.last_checked_at = timezone.now()
        source.save(update_fields=["last_check_message", "last_checked_at", "updated_at"])
        return {"uploaded": 0, "queued": 0, "message": str(exc)}

    known_ids = _known_video_ids_for_source(user, source)
    new_videos = [v for v in data.get("videos", []) if v.get("id") not in known_ids]
    # Prefer Broner / Neon / Kai / Speed / Ray titles first when discovering new clips.
    new_videos = prioritize_videos_by_topic(new_videos, prefer_only=False)

    ready = queue_videos_for_source(source, new_videos, upload_immediately=True)
    retries = _retry_failed_queue_items_for_source(source)
'''

    new_check_mid = '''    try:
        data = fetch_videos(
            source.input_value,
            limit=settings.SCHEDULE_FETCH_LIMIT,
            source=source.source_type,
            user=user,
        )
    except ValueError as exc:
        source.last_check_message = str(exc)
        source.last_checked_at = timezone.now()
        source.save(update_fields=["last_check_message", "last_checked_at", "updated_at"])
        return {"uploaded": 0, "queued": 0, "message": str(exc)}

    fetched_videos = list(data.get("videos") or [])
    fetched_ids = [str(v.get("id") or "").strip() for v in fetched_videos if v.get("id")]

    # First-run (or watchdog enable): remember current listings, upload NOTHING old.
    if not source.baseline_seeded_at:
        return _seed_source_baseline(source, fetched_videos)

    known_ids = _known_video_ids_for_user(user)
    # Only brand-new IDs that appeared after the baseline / previous watches.
    new_videos = [
        v
        for v in fetched_videos
        if str(v.get("id") or "").strip() and str(v.get("id")).strip() not in known_ids
    ]
    # Prefer Broner / Neon / Kai / Speed / Ray titles first when discovering new clips.
    new_videos = prioritize_videos_by_topic(new_videos, prefer_only=False)

    # Always remember what we just saw so older clips never become "new" later.
    _remember_seen_ids(source, fetched_ids)

    ready = queue_videos_for_source(source, new_videos, upload_immediately=True)
    retries = _retry_failed_queue_items_for_source(source)
'''

    if "if not source.baseline_seeded_at:" in text:
        print("check_scheduled_source already watchdog-aware")
    else:
        if old_check_mid not in text:
            raise SystemExit("check_scheduled_source fetch block not found")
        text = text.replace(old_check_mid, new_check_mid, 1)
        print("patched check_scheduled_source")

    # Tighten failed retries: only recent failures (within watch max age)
    old_retry = '''def _retry_failed_queue_items_for_source(source: ScheduledSource) -> list[ScheduledUploadQueue]:
    """Reset failed/stuck queue rows so they upload immediately."""
    now = timezone.now()
    stuck_before = now - timedelta(minutes=20)
    ip_backoff_after = now - timedelta(hours=6)
    items = list(
        ScheduledUploadQueue.objects.filter(
            user=source.user,
            source=source,
            status=ScheduledUploadQueue.Status.FAILED,
        ).order_by("updated_at")
    )
'''
    new_retry = '''def _retry_failed_queue_items_for_source(source: ScheduledSource) -> list[ScheduledUploadQueue]:
    """Reset failed/stuck queue rows so they upload immediately.

    Only retries items created inside the watch window so days-old failures
    are not endlessly re-posted after quota/channel limits clear.
    """
    now = timezone.now()
    stuck_before = now - timedelta(minutes=20)
    ip_backoff_after = now - timedelta(hours=6)
    max_age_hours = int(getattr(settings, "SCHEDULE_WATCH_MAX_AGE_HOURS", 6))
    created_after = now - timedelta(hours=max_age_hours)
    items = list(
        ScheduledUploadQueue.objects.filter(
            user=source.user,
            source=source,
            status=ScheduledUploadQueue.Status.FAILED,
            created_at__gte=created_after,
        ).order_by("updated_at")
    )
'''
    if "created_at__gte=created_after" in text and "SCHEDULE_WATCH_MAX_AGE_HOURS" in text:
        print("retry age filter already present")
    elif old_retry in text:
        text = text.replace(old_retry, new_retry, 1)
        print("patched retry age window")
    else:
        print("WARN: retry function block not exact; skipping age filter inject")

    # Reduce auto priority dump of old feeder clips: only when baseline exists globally
    # Soft-disable aggressive auto queue of 6 random old clips each cycle.
    old_auto = '''    auto_queued = 0
    try:
        auto_queued = auto_queue_priority_clips(user, limit=6)
    except Exception as exc:
        logger.info("Auto priority discovery skipped for %s: %s", user, exc)
'''
    new_auto = '''    auto_queued = 0
    # Disabled: feeder auto-queue was re-posting older clips. Watchdog sources
    # only upload IDs that appear AFTER each source baseline.
    try:
        auto_queued = 0
    except Exception as exc:
        logger.info("Auto priority discovery skipped for %s: %s", user, exc)
'''
    if "feeder auto-queue was re-posting" in text:
        print("auto_queue already disabled")
    elif old_auto in text:
        text = text.replace(old_auto, new_auto, 1)
        print("disabled auto_queue_priority_clips dump")
    else:
        print("WARN: auto_queue block not found")

    RUNNER.write_text(text, encoding="utf-8")
    # Syntax check may fail if imports broken - try compile
    try:
        ast.parse(text)
        print("schedule_runner.py syntax ok")
    except SyntaxError as exc:
        raise SystemExit(f"syntax error after patch: {exc}") from exc


def patch_timer() -> None:
    TIMER.write_text(
        dedent(
            """\
            [Unit]
            Description=Run TikTok Cliper new-clip watchdog every 1 minute

            [Timer]
            OnBootSec=1min
            OnUnitActiveSec=1min
            AccuracySec=15s
            Persistent=true

            [Install]
            WantedBy=timers.target
            """
        ),
        encoding="utf-8",
    )
    print("patched systemd timer to 1 minute")


def run_migration() -> None:
    cmd = [
        str(ROOT / "venv" / "bin" / "python"),
        str(ROOT / "manage.py"),
        "makemigrations",
        "clipper",
        "--name",
        "source_watchdog_baseline",
    ]
    subprocess.check_call(cmd, cwd=str(ROOT))
    subprocess.check_call(
        [str(ROOT / "venv" / "bin" / "python"), str(ROOT / "manage.py"), "migrate", "clipper"],
        cwd=str(ROOT),
    )
    print("migrated")


def reload_services() -> None:
    subprocess.check_call(["systemctl", "daemon-reload"])
    subprocess.check_call(["systemctl", "restart", "tiktok-cliper-schedule.timer"])
    # HUP gunicorn
    import os
    import signal

    out = subprocess.check_output(["pgrep", "-o", "-f", "/root/tiktok-cliper/venv/bin/gunicorn config.wsgi"])
    pid = int(out.decode().strip().splitlines()[0])
    os.kill(pid, signal.SIGHUP)
    print("reloaded timer + gunicorn", pid)


def seed_existing_sources() -> None:
    """On deploy, baseline all active sources so the next minute only gets truly new clips."""
    import os
    import django

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import sys

    sys.path.insert(0, str(ROOT))
    django.setup()
    from clipper.models import ScheduledSource
    from clipper.services.media_fetch import fetch_videos
    from clipper.services.schedule_runner import _seed_source_baseline

    for source in ScheduledSource.objects.filter(is_active=True):
        if source.baseline_seeded_at:
            print("already baselined", source.id, source.display_label)
            continue
        try:
            data = fetch_videos(
                source.input_value,
                limit=int(__import__("django.conf", fromlist=["settings"]).settings.SCHEDULE_FETCH_LIMIT),
                source=source.source_type,
                user=source.user,
            )
            result = _seed_source_baseline(source, list(data.get("videos") or []))
            print("seeded", source.id, source.display_label, result["message"][:80])
        except Exception as exc:
            print("seed failed", source.id, source.display_label, exc)


if __name__ == "__main__":
    patch_models()
    patch_settings()
    write_watch_helpers_into_runner()
    patch_timer()
    run_migration()
    reload_services()
    seed_existing_sources()
    print("DONE")
