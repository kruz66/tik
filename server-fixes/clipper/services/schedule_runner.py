import logging
from datetime import datetime, timedelta
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from clipper.models import ScheduledSource, ScheduledUploadQueue, ScheduledUploadRecord, UploadedVideo
from clipper.services.channel_suggestions import queue_suggested_clips
from clipper.services.clip_publish import publish_clip
from clipper.services.content_safety import ContentSafetyError
from clipper.services.download_cleanup import (
    cleanup_after_publish,
    cleanup_schedule_temp,
)
from clipper.services.limits import check_can_upload
from clipper.services.media_download import download_video
from clipper.services.media_fetch import fetch_videos
from clipper.services.posting_preferences import (
    ai_features_enabled,
    normalize_destinations,
    user_has_any_upload_path,
)
from clipper.services.schedule_service import source_to_dict
from clipper.services.upload_timing import get_next_upload_slots
from clipper.services.video_processing import process_video_for_upload
from clipper.services.youtube import GOOGLE_QUOTA_MESSAGE, format_upload_error
from clipper.services.youtube_metadata import (
    PRIORITY_AUTO_SOURCES,
    prioritize_videos_by_topic,
    title_priority_score,
)
from clipper.services.youtube_quota import (
    format_quota_wait_message,
    get_quota_resume_at,
    handle_youtube_quota_hit,
    is_google_quota_paused,
)
from clipper.services.youtube_upload import YouTubeQuotaError
from clipper.services.youtube_upload import user_has_youtube_upload_capacity
from clipper.services.zernio_youtube import has_zernio_account

logger = logging.getLogger(__name__)



def _is_api_quota_upload_error(exc: Exception) -> bool:
    """True for GCP API quota errors; false for YouTube channel daily upload limits."""
    if isinstance(exc, YouTubeQuotaError):
        return not exc.is_channel_limit
    message = format_upload_error(exc)
    lowered = message.lower()
    if "channel daily upload limit" in lowered or "unverified oauth" in lowered:
        return False
    return "daily api limit" in lowered or (
        "quota" in lowered and "channel" not in lowered
    )


def _defer_uploads_for_quota(
    user, *, resume_at=None, current_item: ScheduledUploadQueue | None = None
) -> datetime:
    target = resume_at or handle_youtube_quota_hit(user)
    if target is None:
        from clipper.services.youtube_quota import next_quota_reset_at

        target = next_quota_reset_at()

    wait_msg = format_quota_wait_message(target)

    if current_item is not None:
        current_item.status = ScheduledUploadQueue.Status.PENDING
        current_item.error_message = wait_msg
        current_item.scheduled_for = target
        current_item.save(
            update_fields=["status", "error_message", "scheduled_for", "updated_at"]
        )

    from django.db.models import Q

    quota_q = (
        Q(error_message__icontains="daily API limit")
        | Q(error_message__icontains="auto-retry scheduled")
    )
    ScheduledUploadQueue.objects.filter(
        user=user,
        status__in=[
            ScheduledUploadQueue.Status.PENDING,
            ScheduledUploadQueue.Status.FAILED,
            ScheduledUploadQueue.Status.UPLOADING,
        ],
    ).filter(quota_q).update(
        status=ScheduledUploadQueue.Status.PENDING,
        error_message=wait_msg,
        scheduled_for=target,
        updated_at=timezone.now(),
    )

    due_qs = ScheduledUploadQueue.objects.filter(
        user=user,
        status=ScheduledUploadQueue.Status.PENDING,
        scheduled_for__lte=timezone.now(),
    )
    if current_item is not None:
        due_qs = due_qs.exclude(pk=current_item.pk)
    due_qs.update(
        scheduled_for=target,
        error_message=wait_msg,
        updated_at=timezone.now(),
    )
    return target


def auto_queue_priority_clips(user, *, limit: int = 8) -> int:
    """Actively find Broner/Neon/Kai/Speed/Ray clips from feeder pages and queue them."""
    from clipper.services.youtube_metadata import PRIORITY_AUTO_SOURCES
    known = set(
        ScheduledUploadRecord.objects.filter(user=user).values_list("source_video_id", flat=True)
    ) | set(
        ScheduledUploadQueue.objects.filter(user=user)
        .exclude(status=ScheduledUploadQueue.Status.FAILED)
        .values_list("source_video_id", flat=True)
    )

    queued = 0
    now = timezone.now()
    for item in PRIORITY_AUTO_SOURCES:
        if queued >= limit:
            break
        handle = (item.get("input") or "").strip()
        source_type = (item.get("source") or "auto").strip().lower()
        if not handle:
            continue
        try:
            data = fetch_videos(handle, source=source_type, limit=12, user=user)
        except Exception as exc:
            logger.info("Auto priority fetch skipped for %s: %s", handle, exc)
            continue

        videos = prioritize_videos_by_topic(data.get("videos") or [], prefer_only=False)
        for video in videos:
            if queued >= limit:
                break
            video_id = str(video.get("id") or "").strip()
            if not video_id or video_id in known:
                continue
            score = title_priority_score(str(video.get("title") or ""))
            if score <= 0 and queued >= max(2, limit // 2):
                continue

            source_row = (
                ScheduledSource.objects.filter(user=user, is_active=True)
                .filter(input_value__icontains=handle.lstrip("@"))
                .first()
            )
            ScheduledUploadQueue.objects.create(
                user=user,
                source=source_row,
                source_video_id=video_id,
                source_url=video.get("url") or "",
                title=video.get("title") or "Auto clip",
                description=video.get("description") or "",
                video_source=video.get("source") or source_type,
                scheduled_for=now,
                is_suggestion=True,
                suggestion_handle=handle,
                suggestion_reason="Auto priority topic discovery",
            )
            known.add(video_id)
            queued += 1
    return queued


def _known_video_ids_for_user(user) -> set[str]:
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


def queue_videos_for_source(
    source: ScheduledSource,
    videos: list[dict],
    *,
    upload_immediately: bool = True,
) -> list[ScheduledUploadQueue]:
    """Queue new videos for immediate download + YouTube upload.

    Returns newly queued (or re-queued failed) rows ready to process now.
    """
    if not videos:
        return []

    now = timezone.now()
    if upload_immediately:
        slots = [now] * len(videos)
    else:
        slots = get_next_upload_slots(source.user, count=len(videos))

    ready: list[ScheduledUploadQueue] = []

    with transaction.atomic():
        for index, video in enumerate(videos):
            video_id = str(video.get("id") or "").strip()
            if not video_id:
                continue

            if ScheduledUploadRecord.objects.filter(
                user=source.user, source_video_id=video_id
            ).exists():
                continue

            scheduled_for = slots[min(index, len(slots) - 1)]
            existing = (
                ScheduledUploadQueue.objects.select_for_update()
                .filter(user=source.user, source_video_id=video_id)
                .first()
            )
            if existing:
                if existing.status in {
                    ScheduledUploadQueue.Status.PENDING,
                    ScheduledUploadQueue.Status.UPLOADING,
                    ScheduledUploadQueue.Status.COMPLETED,
                }:
                    continue

                # Retry previously failed items immediately.
                existing.source = source
                existing.source_url = video.get("url") or existing.source_url
                existing.title = video.get("title") or existing.title
                existing.description = video.get("description") or existing.description
                existing.video_source = video.get("source") or source.source_type
                existing.status = ScheduledUploadQueue.Status.PENDING
                existing.error_message = ""
                existing.scheduled_for = scheduled_for
                existing.save(
                    update_fields=[
                        "source",
                        "source_url",
                        "title",
                        "description",
                        "video_source",
                        "status",
                        "error_message",
                        "scheduled_for",
                        "updated_at",
                    ]
                )
                ready.append(existing)
                continue

            row = ScheduledUploadQueue.objects.create(
                user=source.user,
                source=source,
                source_video_id=video_id,
                source_url=video.get("url") or "",
                title=video.get("title") or "",
                description=video.get("description") or "",
                video_source=video.get("source") or source.source_type,
                scheduled_for=scheduled_for,
            )
            ready.append(row)

    return ready


def process_queue_items(
    items: list[ScheduledUploadQueue],
    *,
    destinations: list[str] | None = None,
) -> dict:
    """Download and publish queued videos immediately to linked destinations."""
    uploaded = 0
    errors: list[str] = []
    quota_hit = False
    quota_message = ""

    known_ids: set[str] = set()
    for item in items:
        if quota_hit:
            break
        if item.status != ScheduledUploadQueue.Status.PENDING:
            continue

        vid = str(item.source_video_id or "").strip()
        if item.user_id not in getattr(process_queue_items, "_known_cache", {}):
            # per-user known set cache for this call
            pass
        if not known_ids:
            known_ids = _known_video_ids_for_user(item.user)
        if not vid or vid in known_ids:
            item.status = ScheduledUploadQueue.Status.FAILED
            item.error_message = "Skipped: video already seen or previously posted."
            item.updated_at = timezone.now()
            item.save(update_fields=["status", "error_message", "updated_at"])
            continue

        if is_google_quota_paused(item.user):
            resume_at = get_quota_resume_at(item.user)
            quota_message = format_quota_wait_message(resume_at)
            quota_hit = True
            _defer_uploads_for_quota(item.user, resume_at=resume_at, current_item=item)
            break

        allowed, limit_message = check_can_upload(item.user, 1)
        if not allowed:
            errors.append(limit_message)
            break

        try:
            # Force due-now semantics for immediate processing.
            if item.scheduled_for > timezone.now():
                item.scheduled_for = timezone.now()
                item.save(update_fields=["scheduled_for", "updated_at"])
            process_queued_upload(item, destinations=destinations)
            uploaded += 1
        except YouTubeQuotaError as exc:
            if exc.is_channel_limit:
                errors.append(str(exc))
                break
            resume_at = _defer_uploads_for_quota(item.user, current_item=item)
            quota_message = format_quota_wait_message(resume_at)
            errors.append(quota_message or str(exc))
            quota_hit = True
            break
        except ContentSafetyError as exc:
            errors.append(str(exc))
        except Exception as exc:
            logger.exception("Queued upload failed for %s", item.source_video_id)
            message = format_upload_error(exc)
            if _is_api_quota_upload_error(exc):
                resume_at = _defer_uploads_for_quota(item.user, current_item=item)
                quota_message = format_quota_wait_message(resume_at)
                errors.append(quota_message)
                quota_hit = True
                break
            errors.append(message)

    if uploaded:
        msg = f"Downloaded and uploaded {uploaded} video(s) to YouTube."
    elif quota_message:
        msg = quota_message
    elif errors:
        msg = errors[0]
    else:
        msg = "No videos were uploaded."

    return {
        "uploaded": uploaded,
        "message": msg,
        "errors": errors,
        "quota_paused": quota_hit,
    }


def process_queued_upload(
    queue_item: ScheduledUploadQueue,
    *,
    destinations: list[str] | None = None,
) -> ScheduledUploadRecord | None:
    """Download at upload time, publish, then delete all local files."""
    user = queue_item.user
    targets = normalize_destinations(user, destinations)
    if not targets:
        raise ValueError("Link YouTube before scheduled uploads can run.")

    download_dir = Path(settings.DOWNLOADS_DIR) / "schedule" / "_tmp" / str(queue_item.id)
    download_dir.mkdir(parents=True, exist_ok=True)

    queue_item.status = ScheduledUploadQueue.Status.UPLOADING
    queue_item.error_message = ""
    queue_item.save(update_fields=["status", "error_message", "updated_at"])

    raw_filepath = ""
    processed_filepath = ""

    try:
        existing = ScheduledUploadRecord.objects.filter(
            user=user, source_video_id=queue_item.source_video_id
        ).first()
        if existing:
            queue_item.status = ScheduledUploadQueue.Status.COMPLETED
            queue_item.error_message = ""
            queue_item.save(update_fields=["status", "error_message", "updated_at"])
            return existing

        result = download_video(
            queue_item.source_url,
            str(download_dir),
            queue_item.source_video_id,
            source=queue_item.video_source,
            user=user,
        )
        raw_filepath = result.get("filepath") or ""
        if not raw_filepath or not Path(raw_filepath).is_file():
            raise FileNotFoundError(
                f"Download finished but video file is missing for {queue_item.source_video_id}."
            )

        title = result.get("title") or queue_item.title or "Scheduled upload"
        description = (
            result.get("description")
            or queue_item.description
            or title
        )

        # Same rule as Clip & Upload: enhancements only when Enable AI is on.
        if ai_features_enabled(user):
            processed_filepath = process_video_for_upload(
                raw_filepath,
                user,
                title=title,
                description=description,
                destinations=targets,
            )
            if not processed_filepath or not Path(processed_filepath).is_file():
                raise FileNotFoundError("Processed video file is missing before publish.")
        else:
            processed_filepath = raw_filepath

        publish_result = publish_clip(
            user,
            processed_filepath,
            title=title,
            description=description,
            source=queue_item.video_source,
            destinations=targets,
        )

        yt_result = publish_result.get("youtube") or {}
        tt_result = publish_result.get("tiktok") or {}

        record, created = ScheduledUploadRecord.objects.get_or_create(
            user=user,
            source_video_id=queue_item.source_video_id,
            defaults={
                "source": queue_item.source,
                "source_url": queue_item.source_url,
                "title": title,
                "youtube_video_id": yt_result.get("video_id") or "",
                "youtube_url": yt_result.get("url") or "",
                "tiktok_post_id": tt_result.get("post_id") or tt_result.get("publish_id") or "",
                "tiktok_url": tt_result.get("url") or "",
            },
        )

        if created and queue_item.source:
            queue_item.source.uploads_count += 1
            queue_item.source.save(update_fields=["uploads_count", "updated_at"])

        queue_item.status = ScheduledUploadQueue.Status.COMPLETED
        queue_item.save(update_fields=["status", "updated_at"])
        return record
    except YouTubeQuotaError as exc:
        if exc.is_channel_limit:
            queue_item.status = ScheduledUploadQueue.Status.FAILED
            queue_item.error_message = exc.message
            queue_item.save(update_fields=["status", "error_message", "updated_at"])
            raise
        queue_item.status = ScheduledUploadQueue.Status.PENDING
        queue_item.save(update_fields=["status", "updated_at"])
        raise
    except Exception as exc:
        message = format_upload_error(exc)
        if _is_api_quota_upload_error(exc):
            queue_item.status = ScheduledUploadQueue.Status.PENDING
            queue_item.error_message = message
            queue_item.save(update_fields=["status", "error_message", "updated_at"])
            raise YouTubeQuotaError(message) from exc
        queue_item.status = ScheduledUploadQueue.Status.FAILED
        queue_item.error_message = message
        queue_item.save(update_fields=["status", "error_message", "updated_at"])
        raise
    finally:
        cleanup_after_publish(raw_filepath, processed_filepath)
        cleanup_schedule_temp(queue_item.id)


def process_due_uploads_for_user(user, *, limit: int = 5) -> dict:
    if not user_has_any_upload_path(user):
        return {"uploaded": 0, "message": "Link YouTube or TikTok before scheduled uploads can run."}

    if is_google_quota_paused(user):
        resume_at = get_quota_resume_at(user)
        message = format_quota_wait_message(resume_at)
        _defer_uploads_for_quota(user, resume_at=resume_at)
        return {
            "uploaded": 0,
            "message": message,
            "quota_paused": True,
            "resume_at": resume_at.isoformat() if resume_at else None,
        }

    destinations = normalize_destinations(user, None)
    if destinations == ["youtube"] and not user_has_youtube_upload_capacity(user) and not has_zernio_account(user):
        resume_at = handle_youtube_quota_hit(user)
        message = format_quota_wait_message(resume_at)
        _defer_uploads_for_quota(user, resume_at=resume_at)
        return {
            "uploaded": 0,
            "message": message,
            "quota_paused": True,
            "resume_at": resume_at.isoformat() if resume_at else None,
        }

    # Reclaim uploads that died mid-flight (server restart, crash, etc.).
    stuck_before = timezone.now() - timedelta(minutes=20)
    ScheduledUploadQueue.objects.filter(
        user=user,
        status=ScheduledUploadQueue.Status.UPLOADING,
        updated_at__lte=stuck_before,
    ).update(
        status=ScheduledUploadQueue.Status.PENDING,
        error_message="",
        scheduled_for=timezone.now(),
        updated_at=timezone.now(),
    )

    # Only process recently queued items so hours/days-old backlog never uploads.
    now = timezone.now()
    max_age_h = max(1, int(getattr(settings, "SCHEDULE_WATCH_MAX_AGE_HOURS", 6) or 6))
    newest_allowed = now - timedelta(hours=max_age_h)
    ScheduledUploadQueue.objects.filter(
        user=user,
        status__in=[
            ScheduledUploadQueue.Status.PENDING,
            ScheduledUploadQueue.Status.FAILED,
            ScheduledUploadQueue.Status.UPLOADING,
        ],
        created_at__lt=newest_allowed,
    ).update(
        status=ScheduledUploadQueue.Status.FAILED,
        error_message=f"Cancelled: outside new-clip watch window (older than {max_age_h}h).",
        scheduled_for=now,
        updated_at=now,
    )
    due = list(
        ScheduledUploadQueue.objects.filter(
            user=user,
            status=ScheduledUploadQueue.Status.PENDING,
            scheduled_for__lte=now,
            created_at__gte=newest_allowed,
        ).order_by("scheduled_for")[:limit]
    )

    uploaded = 0
    errors = []
    quota_hit = False
    quota_message = ""

    known_ids = _known_video_ids_for_user(user)
    for item in due:
        if quota_hit:
            break

        # Never upload a clip that was already seen/posted (hours/days-old or re-queued).
        vid = str(item.source_video_id or "").strip()
        if not vid or vid in known_ids:
            item.status = ScheduledUploadQueue.Status.FAILED
            item.error_message = "Skipped: video already seen or previously posted."
            item.updated_at = timezone.now()
            item.save(update_fields=["status", "error_message", "updated_at"])
            continue

        allowed, limit_message = check_can_upload(user, 1)
        if not allowed:
            errors.append(limit_message)
            break

        try:
            process_queued_upload(item, destinations=destinations)
            uploaded += 1
            known_ids.add(vid)
        except YouTubeQuotaError as exc:
            if exc.is_channel_limit:
                errors.append(str(exc))
                break
            resume_at = _defer_uploads_for_quota(user, current_item=item)
            quota_message = format_quota_wait_message(resume_at)
            errors.append(quota_message)
            quota_hit = True
            break
        except ContentSafetyError as exc:
            errors.append(str(exc))
        except Exception as exc:
            logger.exception("Queued upload failed for %s", item.source_video_id)
            message = format_upload_error(exc)
            if _is_api_quota_upload_error(exc):
                resume_at = _defer_uploads_for_quota(user, current_item=item)
                quota_message = format_quota_wait_message(resume_at)
                errors.append(quota_message)
                quota_hit = True
                break
            errors.append(message)

    if uploaded:
        msg = f"Uploaded {uploaded} scheduled video(s)."
    elif quota_message:
        msg = quota_message
    elif due and errors:
        msg = errors[0]
    elif due:
        msg = "Scheduled uploads failed."
    else:
        msg = "No uploads due right now."

    return {
        "uploaded": uploaded,
        "message": msg,
        "due_count": len(due),
        "quota_paused": quota_hit,
    }


def _retry_failed_queue_items_for_source(source: ScheduledSource) -> list[ScheduledUploadQueue]:
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
    stuck = list(
        ScheduledUploadQueue.objects.filter(
            user=source.user,
            source=source,
            status=ScheduledUploadQueue.Status.UPLOADING,
            updated_at__lte=stuck_before,
        ).order_by("updated_at")
    )
    items.extend(stuck)

    ready: list[ScheduledUploadQueue] = []
    known_ids = _known_video_ids_for_user(source.user)
    for item in items:
        vid = str(item.source_video_id or "").strip()
        err = (item.error_message or "").lower()
        # Permanent skips: already posted/seen, or cancelled by watchdog cleanup.
        if (
            not vid
            or vid in known_ids
            or "already seen" in err
            or "previously posted" in err
            or "cancelled:" in err
            or "watchdog" in err
            or "outside new-clip watch window" in err
        ):
            item.status = ScheduledUploadQueue.Status.FAILED
            if "already seen" not in err and "cancelled:" not in err and "watchdog" not in err:
                item.error_message = "Skipped: video already seen or previously posted."
            item.save(update_fields=["status", "error_message", "updated_at"])
            continue

        if ScheduledUploadRecord.objects.filter(
            user=source.user, source_video_id=item.source_video_id
        ).exists():
            item.status = ScheduledUploadQueue.Status.COMPLETED
            item.error_message = ""
            item.save(update_fields=["status", "error_message", "updated_at"])
            continue

        if (
            item.status == ScheduledUploadQueue.Status.FAILED
            and "ip address is blocked" in err
            and item.updated_at
            and item.updated_at > ip_backoff_after
        ):
            continue

        resume_at = now
        if is_google_quota_paused(source.user) or "daily api limit" in err or "quota" in err:
            resume_at = get_quota_resume_at(source.user) or handle_youtube_quota_hit(source.user) or now
            item.error_message = format_quota_wait_message(resume_at)
        else:
            item.error_message = ""

        item.status = ScheduledUploadQueue.Status.PENDING
        item.scheduled_for = resume_at
        item.save(update_fields=["status", "error_message", "scheduled_for", "updated_at"])
        ready.append(item)
    return ready


def check_scheduled_source(source: ScheduledSource) -> dict:
    """Find new videos, then download and upload them to YouTube immediately."""
    user = source.user
    if not user_has_any_upload_path(user):
        source.last_check_message = "Link YouTube or TikTok before scheduled uploads can run."
        source.last_checked_at = timezone.now()
        source.save(update_fields=["last_check_message", "last_checked_at", "updated_at"])
        return {"uploaded": 0, "queued": 0, "message": source.last_check_message}

    destinations = normalize_destinations(user, None)
    if is_google_quota_paused(user) or (
        destinations == ["youtube"] and not user_has_youtube_upload_capacity(user) and not has_zernio_account(user)
    ):
        resume_at = get_quota_resume_at(user) or handle_youtube_quota_hit(user)
        message = format_quota_wait_message(resume_at)
        source.last_check_message = message
        source.last_checked_at = timezone.now()
        source.save(update_fields=["last_check_message", "last_checked_at", "updated_at"])
        # Still discover + queue while paused; uploads wait until resume_at.
    else:
        message = ""

    try:
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
    seen_ids = {item.id for item in ready}
    for item in retries:
        if item.id not in seen_ids:
            ready.append(item)
            seen_ids.add(item.id)

    queued_count = len(ready)
    uploaded = 0
    quota_paused = is_google_quota_paused(user) or (
        destinations == ["youtube"] and not user_has_youtube_upload_capacity(user) and not has_zernio_account(user)
    )

    if ready and quota_paused:
        resume_at = get_quota_resume_at(user) or handle_youtube_quota_hit(user)
        msg = format_quota_wait_message(resume_at)
        ScheduledUploadQueue.objects.filter(id__in=[row.id for row in ready]).update(
            status=ScheduledUploadQueue.Status.PENDING,
            scheduled_for=resume_at,
            error_message=msg,
            updated_at=timezone.now(),
        )
        source.last_check_message = f"Queued {queued_count} clip(s). {msg}"
        source.last_checked_at = timezone.now()
        source.save(update_fields=["last_check_message", "last_checked_at", "updated_at"])
        return {
            "uploaded": 0,
            "queued": queued_count,
            "message": source.last_check_message,
            "new_found": len(new_videos),
            "quota_paused": True,
        }

    if ready:
        # Prefer brand-new finds first; process a bounded batch right away.
        immediate_limit = max(5, len([v for v in new_videos]))
        to_process = ready[:immediate_limit]
        upload_result = process_queue_items(to_process, destinations=destinations)
        uploaded = upload_result.get("uploaded", 0)
        if uploaded:
            msg = upload_result["message"]
            if uploaded < len(to_process) and upload_result.get("errors"):
                msg = f"{msg} Some failed: {upload_result['errors'][0]}"
            elif queued_count > len(to_process):
                msg = f"{msg} {queued_count - len(to_process)} more queued to upload next."
        elif upload_result.get("quota_paused"):
            msg = upload_result.get("message") or format_quota_wait_message(get_quota_resume_at(user))
            quota_paused = True
        elif upload_result.get("errors"):
            msg = (
                f"Found {queued_count} video(s) to upload but failed: "
                f"{upload_result['errors'][0]}"
            )
        else:
            msg = f"Queued {queued_count} video(s); upload did not complete."
    elif new_videos:
        msg = "Found new videos but could not queue them."
    elif message:
        msg = message
    else:
        msg = "No new videos."

    source.last_check_message = msg
    source.last_checked_at = timezone.now()
    source.save(update_fields=["last_check_message", "last_checked_at", "updated_at"])

    return {
        "uploaded": uploaded,
        "queued": queued_count,
        "message": msg,
        "new_found": len(new_videos),
        "quota_paused": quota_paused,
    }


def check_user_schedules(user) -> dict:
    # Only check sources the user explicitly added — no auto-adding.
    # Make any previously delayed pending items due now (unless quota-paused).
    now = timezone.now()
    if is_google_quota_paused(user):
        # Fast path: keep uploads deferred until quota reset. Skip discovery so the
        # 2-minute timer is not blocked for minutes while we cannot upload.
        resume_at = get_quota_resume_at(user) or handle_youtube_quota_hit(user)
        message = format_quota_wait_message(resume_at)
        _defer_uploads_for_quota(user, resume_at=resume_at)
        for source in ScheduledSource.objects.filter(user=user, is_active=True):
            source.last_check_message = message
            source.last_checked_at = now
            source.save(update_fields=["last_check_message", "last_checked_at", "updated_at"])
        return {
            "uploaded": 0,
            "queued": 0,
            "message": message,
            "sources": [],
            "pending_count": pending_uploads_count(user),
            "quota_paused": True,
            "resume_at": resume_at.isoformat() if resume_at else None,
        }

    ScheduledUploadQueue.objects.filter(
        user=user,
        status=ScheduledUploadQueue.Status.PENDING,
        scheduled_for__gt=now,
    ).exclude(
        error_message__icontains="auto-retry scheduled"
    ).update(scheduled_for=now, updated_at=now)

    auto_queued = 0
    # Disabled: feeder auto-queue was re-posting older clips. Watchdog sources
    # only upload IDs that appear AFTER each source baseline.
    try:
        auto_queued = 0
    except Exception as exc:
        logger.info("Auto priority discovery skipped for %s: %s", user, exc)

    upload_result = process_due_uploads_for_user(user)
    suggested = 0
    # Disabled: analytics suggestions re-queue older clips. Watchdog only posts
    # brand-new IDs discovered on watched sources after baseline.
    try:
        suggested = 0
    except Exception as exc:
        logger.info("Suggested clip discovery skipped for %s: %s", user, exc)

    sources = ScheduledSource.objects.filter(user=user, is_active=True)
    total_queued = auto_queued + suggested
    total_uploaded = upload_result.get("uploaded", 0)
    results = []
    source_messages = []
    quota_paused = bool(upload_result.get("quota_paused"))

    for source in sources:
        result = check_scheduled_source(source)
        total_queued += result.get("queued", 0)
        total_uploaded += result.get("uploaded", 0)
        if result.get("quota_paused"):
            quota_paused = True
        if result.get("queued") or result.get("uploaded") or (
            result.get("message") and result["message"] != "No new videos."
        ):
            source_messages.append(result["message"])
        results.append({"source": source_to_dict(source), **result})

    if total_uploaded:
        message = f"Downloaded and uploaded {total_uploaded} video(s) to YouTube."
    elif quota_paused:
        message = upload_result.get("message") or format_quota_wait_message(get_quota_resume_at(user))
    elif source_messages:
        message = source_messages[0]
    else:
        message = upload_result.get("message") or "Schedule check complete."

    if auto_queued or suggested:
        message = (
            f"{message} Auto-queued {auto_queued + suggested} clip(s) "
            f"(priority={auto_queued}, analytics={suggested})."
        )

    # Catch items queued as due-now during source checks (skip if still quota-paused).
    if not is_google_quota_paused(user):
        followup = process_due_uploads_for_user(user)
        followup_uploaded = followup.get("uploaded", 0)
        if followup_uploaded:
            total_uploaded += followup_uploaded
            message = f"Downloaded and uploaded {total_uploaded} video(s) to YouTube."
        if followup.get("quota_paused"):
            quota_paused = True
            message = followup.get("message") or message

    return {
        "uploaded": total_uploaded,
        "queued": total_queued,
        "message": message,
        "sources": results,
        "pending_count": pending_uploads_count(user),
        "quota_paused": quota_paused,
        "resume_at": (
            get_quota_resume_at(user).isoformat() if get_quota_resume_at(user) else None
        ),
    }


def check_all_active_schedules() -> dict:
    from django.contrib.auth import get_user_model

    User = get_user_model()
    active_users = (
        ScheduledSource.objects.filter(is_active=True)
        .values_list("user_id", flat=True)
        .distinct()
    )
    suggestion_users = User.objects.filter(youtube_connection__isnull=False).values_list("id", flat=True)
    user_ids = set(active_users) | set(suggestion_users)

    total_uploaded = 0
    total_queued = 0
    checked = 0
    quota_paused_users = 0

    for user_id in user_ids:
        user = User.objects.get(id=user_id)
        result = check_user_schedules(user)
        total_uploaded += result.get("uploaded", 0)
        total_queued += result.get("queued", 0)
        checked += 1
        if result.get("quota_paused"):
            quota_paused_users += 1
            logger.info(
                "User %s quota-paused until %s — %s",
                user,
                result.get("resume_at"),
                result.get("message"),
            )

    return {
        "checked": checked,
        "uploaded": total_uploaded,
        "queued": total_queued,
        "quota_paused_users": quota_paused_users,
    }


def pending_uploads_count(user) -> int:
    return ScheduledUploadQueue.objects.filter(
        user=user, status=ScheduledUploadQueue.Status.PENDING
    ).count()


def pending_uploads(user, limit: int = 30) -> list[dict]:
    rows = ScheduledUploadQueue.objects.filter(
        user=user, status=ScheduledUploadQueue.Status.PENDING
    ).select_related("source").order_by("scheduled_for")[:limit]
    return [
        {
            "id": row.id,
            "title": row.title,
            "source_url": row.source_url,
            "source_label": row.source.display_label if row.source else row.suggestion_handle,
            "scheduled_for": row.scheduled_for.isoformat(),
            "is_suggestion": row.is_suggestion,
            "suggestion_reason": row.suggestion_reason,
            "video_source": row.video_source,
        }
        for row in rows
    ]


def recent_uploads(user, limit: int = 30) -> list[dict]:
    records = ScheduledUploadRecord.objects.filter(user=user).select_related("source")[:limit]
    return [
        {
            "id": record.id,
            "source_label": record.source.display_label if record.source else "Suggested",
            "source_type": record.source.source_type if record.source else "",
            "title": record.title,
            "source_url": record.source_url,
            "youtube_url": record.youtube_url,
            "tiktok_url": record.tiktok_url,
            "uploaded_at": record.uploaded_at.isoformat(),
        }
        for record in records
    ]
