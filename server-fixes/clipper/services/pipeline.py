import logging
from pathlib import Path

from django.conf import settings

from clipper.models import UploadJob, UploadedVideo
from clipper.services.ai_upload_retry import fix_and_upload_with_ai_retry
from clipper.services.download_cleanup import cleanup_job_downloads
from clipper.services.job_cancel import job_was_cancelled
from clipper.services.clip_publish import format_publish_error, publish_clip
from clipper.services.content_safety import ContentSafetyError
from clipper.services.media_download import download_video
from clipper.services.posting_preferences import ai_features_enabled as user_ai_enabled
from clipper.services.video_processing import process_video_for_upload
from clipper.services.youtube_comments import post_pinned_comment, should_post_pinned_comment

logger = logging.getLogger(__name__)


def _format_job_error(exc: Exception) -> str:
    return format_publish_error(exc)


def _update_job(job: UploadJob, **fields):
    for key, value in fields.items():
        setattr(job, key, value)
    job.save(update_fields=list(fields.keys()) + ["updated_at"])


def _upload_description(item: dict, download_result: dict) -> str:
    return (
        download_result.get("description")
        or item.get("description")
        or item.get("title")
        or ""
    ).strip()


def start_clip_upload_job(
    video_items: list[dict],
    username: str,
    credentials_json: str,
    user=None,
    destinations=None,
    *,
    use_ai: bool | None = None,
    workspace_snapshot: dict | None = None,
) -> UploadJob:
    import threading

    from clipper.services.posting_preferences import normalize_destinations
    from clipper.services.youtube_upload import user_has_youtube_upload_capacity
    from clipper.services.youtube_quota import (
        format_quota_wait_message,
        get_quota_resume_at,
        handle_youtube_quota_hit,
        is_google_quota_paused,
    )
    from clipper.services.zernio_youtube import has_zernio_account

    targets = normalize_destinations(user, destinations)
    if (
        user
        and "youtube" in targets
        and targets == ["youtube"]
        and (is_google_quota_paused(user) or (not user_has_youtube_upload_capacity(user) and not has_zernio_account(user)))
    ):
        resume_at = get_quota_resume_at(user) or handle_youtube_quota_hit(user)
        message = format_quota_wait_message(resume_at)
        job = UploadJob.objects.create(
            user=user,
            total_videos=len(video_items),
            tiktok_username=username,
            workspace_snapshot=workspace_snapshot or {},
            status=UploadJob.Status.FAILED,
            progress_stage="failed",
            progress_message=message,
            error_message=message,
            fix_progress=0,
        )
        return job

    job = UploadJob.objects.create(
        user=user,
        total_videos=len(video_items),
        tiktok_username=username,
        workspace_snapshot=workspace_snapshot or {},
    )
    ai_on = use_ai if use_ai is not None else user_ai_enabled(user)
    target = run_clip_upload_job if ai_on else run_simple_clip_upload_job
    thread = threading.Thread(
        target=target,
        args=(str(job.id), video_items, username, credentials_json, destinations),
        daemon=True,
    )
    thread.start()
    return job


def _youtube_upload_blocked_message(user, destinations=None) -> str | None:
    """Return a wait message when YouTube uploads cannot proceed right now."""
    from clipper.services.posting_preferences import normalize_destinations
    from clipper.services.youtube_upload import user_has_youtube_upload_capacity
    from clipper.services.youtube_quota import (
        format_quota_wait_message,
        get_quota_resume_at,
        handle_youtube_quota_hit,
        is_google_quota_paused,
    )
    from clipper.services.zernio_youtube import has_zernio_account

    targets = normalize_destinations(user, destinations)
    if "youtube" not in targets:
        return None
    if is_google_quota_paused(user):
        return format_quota_wait_message(get_quota_resume_at(user))
    if targets == ["youtube"] and not user_has_youtube_upload_capacity(user) and not has_zernio_account(user):
        resume_at = handle_youtube_quota_hit(user)
        return format_quota_wait_message(resume_at)
    return None


def run_simple_clip_upload_job(
    job_id: str,
    video_items: list[dict],
    username: str,
    credentials_json: str,
    destinations=None,
):
    """Download, apply basic enhancements, and publish without AI."""
    job = UploadJob.objects.get(id=job_id)
    user = job.user
    download_dir = Path(settings.DOWNLOADS_DIR) / str(job_id)
    download_dir.mkdir(parents=True, exist_ok=True)

    blocked = _youtube_upload_blocked_message(user, destinations)
    if blocked:
        _update_job(
            job,
            status=UploadJob.Status.FAILED,
            error_message=blocked,
            progress_message=blocked,
            progress_stage="failed",
            fix_progress=0,
        )
        return

    _update_job(
        job,
        status=UploadJob.Status.DOWNLOADING,
        total_videos=len(video_items),
        completed_videos=0,
        progress_message=f"Preparing {len(video_items)} video(s)...",
        progress_stage="downloading",
        tiktok_username=username,
        fix_progress=5,
    )

    last_error = ""
    for index, item in enumerate(video_items, start=1):
        if job_was_cancelled(job):
            return
        video_url = item.get("url", "")
        video_id = item.get("id", "")
        display_title = item.get("title") or f"Video {video_id}"
        video_source = str(item.get("source") or "tiktok")
        record = UploadedVideo.objects.create(
            job=job,
            tiktok_url=video_url,
            tiktok_id=video_id,
            title=display_title,
            status="downloading",
        )
        try:
            _update_job(
                job,
                current_video_title=display_title,
                progress_stage="downloading",
                progress_message="Downloading source video...",
                fix_progress=15,
            )
            download = download_video(
                video_url,
                str(download_dir),
                video_id,
                source=video_source,
                user=user,
            )
            title = download.get("title") or display_title
            description = _upload_description(item, download)

            # Simple (AI-off) path: download → upload immediately, no enhancements.
            processed = download["filepath"]

            _update_job(
                job,
                status=UploadJob.Status.UPLOADING,
                progress_stage="uploading",
                progress_message=f"Publishing video {index}/{len(video_items)}...",
                fix_progress=55,
            )
            publish_result = publish_clip(
                user,
                processed,
                title=title,
                description=description,
                source=video_source,
                destinations=destinations or item.get("destinations"),
            )
            yt_result = publish_result.get("youtube") or {}
            tt_result = publish_result.get("tiktok") or {}

            record.title = title
            record.youtube_video_id = yt_result.get("video_id") or ""
            record.youtube_url = yt_result.get("url") or ""
            record.tiktok_post_id = tt_result.get("post_id") or tt_result.get("publish_id") or ""
            record.tiktok_url = tt_result.get("url") or ""
            record.status = "completed"
            record.save(
                update_fields=[
                    "title",
                    "youtube_video_id",
                    "youtube_url",
                    "tiktok_post_id",
                    "tiktok_url",
                    "status",
                ]
            )

            progress_message = f"Published {index}/{len(video_items)}."
            tiktok_note = (tt_result.get("message") or "").strip()
            if tiktok_note:
                progress_message = f"{progress_message} {tiktok_note}"

            _update_job(
                job,
                completed_videos=index,
                progress_stage="uploading" if index < len(video_items) else "complete",
                progress_message=progress_message,
                fix_progress=min(95, int(15 + (80 * index / max(len(video_items), 1)))),
            )
        except Exception as exc:
            logger.exception("Simple clip upload failed for %s", video_id)
            last_error = _format_job_error(exc)
            record.status = "failed"
            record.error_message = last_error
            record.save(update_fields=["status", "error_message"])
            # Keep going through remaining clips instead of looking stuck at 15%.
            _update_job(
                job,
                completed_videos=index,
                progress_stage="downloading" if index < len(video_items) else "failed",
                progress_message=(
                    f"Skipped failed clip {index}/{len(video_items)}: {last_error}"
                    if index < len(video_items)
                    else last_error
                ),
                fix_progress=min(95, int(15 + (80 * index / max(len(video_items), 1)))),
            )
            # Stop burning downloads when YouTube quota is already exhausted.
            if "daily api limit" in last_error.lower() or "auto-retry scheduled" in last_error.lower():
                for remaining in video_items[index:]:
                    UploadedVideo.objects.create(
                        job=job,
                        tiktok_url=remaining.get("url", ""),
                        tiktok_id=remaining.get("id", ""),
                        title=remaining.get("title") or remaining.get("id") or "Video",
                        status="failed",
                        error_message=last_error,
                    )
                _update_job(
                    job,
                    status=UploadJob.Status.FAILED,
                    error_message=last_error,
                    progress_message=last_error,
                    progress_stage="failed",
                    fix_progress=0,
                )
                cleanup_job_downloads(job_id)
                return

    failed = job.videos.filter(status="failed").count()
    if failed == job.total_videos:
        _update_job(
            job,
            status=UploadJob.Status.FAILED,
            error_message=last_error or "All uploads failed.",
            progress_message=last_error or "Job failed.",
            progress_stage="failed",
            fix_progress=0,
        )
    elif failed > 0:
        _update_job(job, status=UploadJob.Status.COMPLETED, progress_message=f"Done with {failed} failure(s).")
    else:
        _update_job(job, status=UploadJob.Status.COMPLETED, progress_message="All videos published!")
    cleanup_job_downloads(job_id)


def run_clip_upload_job(
    job_id: str,
    video_items: list[dict],
    username: str,
    credentials_json: str,
    destinations=None,
):
    """Clip Panel upload: download, AI-check/fix with Ollama, then upload."""
    job = UploadJob.objects.get(id=job_id)
    run_ai_fix_upload_job(job_id, video_items, username, job.user, destinations=destinations)


def run_ai_fix_upload_job(job_id: str, video_items: list[dict], username: str, user, destinations=None):
    job = UploadJob.objects.get(id=job_id)
    download_dir = Path(settings.DOWNLOADS_DIR) / str(job_id)
    download_dir.mkdir(parents=True, exist_ok=True)

    blocked = _youtube_upload_blocked_message(user, destinations)
    if blocked:
        _update_job(
            job,
            status=UploadJob.Status.FAILED,
            error_message=blocked,
            progress_message=blocked,
            progress_stage="failed",
            fix_progress=0,
        )
        return

    _update_job(
        job,
        status=UploadJob.Status.DOWNLOADING,
        total_videos=len(video_items),
        completed_videos=0,
        progress_message=f"AI checking and preparing {len(video_items)} video(s)...",
        tiktok_username=username,
    )

    last_error = ""
    for index, item in enumerate(video_items, start=1):
        if job_was_cancelled(job):
            return
        video_url = item.get("url", "")
        video_id = item.get("id", "")
        display_title = item.get("title") or f"Video {video_id}"
        record = UploadedVideo.objects.create(
            job=job,
            tiktok_url=video_url,
            tiktok_id=video_id,
            title=display_title,
            status="downloading",
        )
        try:
            def report_progress(stage: str, message: str, percent: int) -> None:
                _update_job(
                    job,
                    progress_stage=stage,
                    progress_message=message,
                    fix_progress=max(0, min(100, percent)),
                )

            _update_job(
                job,
                current_video_title=display_title,
                progress_stage="downloading",
                progress_message="Starting AI fix pipeline...",
                fix_progress=5,
            )
            result = fix_and_upload_with_ai_retry(
                item,
                user,
                download_dir=str(download_dir),
                check_result=item.get("check_result"),
                on_progress=report_progress,
                destinations=destinations or item.get("destinations"),
                job_id=str(job.id),
                record_id=record.id,
            )
            title = result["title"]
            description = result["description"]
            filepath = result["filepath"]
            video_source = result["source"]
            yt_result = result.get("youtube") or {}
            tt_result = result.get("tiktok") or {}

            if result.get("tiktok_pending"):
                snapshot = dict(job.workspace_snapshot or {})
                snapshot["tiktok_pending_upload"] = {
                    **tt_result,
                    "caption": description or title,
                    "record_id": record.id,
                    "filename": Path(filepath).name if filepath else "clip.mp4",
                }
                snapshot["pipeline_resume"] = {
                    "username": username,
                    "destinations": destinations or item.get("destinations") or [],
                    "remaining_items": video_items[index - 1 :],
                    "completed_count": index - 1,
                }
                record.status = "uploading"
                record.title = title
                record.save(update_fields=["status", "title"])
                _update_job(
                    job,
                    status=UploadJob.Status.UPLOADING,
                    current_video_title=title,
                    progress_stage="tiktok_extension",
                    progress_message="Posting to TikTok via your browser extension...",
                    fix_progress=92,
                    workspace_snapshot=snapshot,
                )
                return

            fix_actions = [
                f"Completed after {len(result.get('attempts') or [])} AI attempt(s)"
            ]

            record.status = "uploading"
            record.title = title
            record.save(update_fields=["status", "title"])

            _update_job(
                job,
                status=UploadJob.Status.UPLOADING,
                current_video_title=title,
                progress_stage="uploading",
                progress_message=f"Upload finished for video {index}/{len(video_items)}.",
                fix_progress=95,
            )

            record.youtube_video_id = yt_result.get("video_id") or ""
            record.youtube_url = yt_result.get("url") or ""
            record.tiktok_post_id = tt_result.get("post_id") or tt_result.get("publish_id") or ""
            record.tiktok_url = tt_result.get("url") or ""
            record.status = "completed"
            record.save(
                update_fields=[
                    "youtube_video_id",
                    "youtube_url",
                    "tiktok_post_id",
                    "tiktok_url",
                    "status",
                ]
            )

            fix_note = "; ".join(fix_actions[:2])
            progress_message = f"Fixed & published {index}/{len(video_items)}."
            if fix_note:
                progress_message = f"{progress_message} {fix_note}"

            tiktok_note = (tt_result.get("message") or "").strip()
            if tiktok_note:
                progress_message = f"{progress_message} {tiktok_note}"

            comment_note = ""
            if yt_result.get("video_id") and should_post_pinned_comment(user):
                _, comment_note = post_pinned_comment(user, yt_result["video_id"])
            if comment_note:
                progress_message = f"{progress_message} {comment_note}"

            _update_job(
                job,
                completed_videos=index,
                progress_stage="complete",
                progress_message=progress_message,
                fix_progress=100,
            )
        except ContentSafetyError as exc:
            last_error = str(exc)
            record.status = "failed"
            record.error_message = last_error
            record.save(update_fields=["status", "error_message"])
            _update_job(
                job,
                progress_stage="failed",
                progress_message=f"Blocked: {last_error}",
                fix_progress=0,
            )
        except Exception as exc:
            logger.exception("AI fix upload failed for %s", video_id)
            last_error = _format_job_error(exc)
            record.status = "failed"
            record.error_message = last_error
            record.save(update_fields=["status", "error_message"])
            _update_job(
                job,
                progress_stage="failed",
                progress_message=last_error,
                fix_progress=0,
            )

    failed = job.videos.filter(status="failed").count()
    if failed == job.total_videos:
        _update_job(
            job,
            status=UploadJob.Status.FAILED,
            error_message=last_error or "All AI fix uploads failed.",
            progress_message=last_error or "Job failed.",
            progress_stage="failed",
            fix_progress=0,
        )
    elif failed > 0:
        _update_job(job, status=UploadJob.Status.COMPLETED, progress_message=f"Done with {failed} failure(s).")
    else:
        _update_job(job, status=UploadJob.Status.COMPLETED, progress_message="All videos fixed and uploaded!")
    cleanup_job_downloads(job_id)


def resume_ai_fix_upload_after_tiktok(job_id: str) -> None:
    import threading

    job = UploadJob.objects.get(id=job_id)
    resume = (job.workspace_snapshot or {}).get("pipeline_resume") or {}
    remaining_items = list(resume.get("remaining_items") or [])
    if len(remaining_items) <= 1:
        _update_job(
            job,
            status=UploadJob.Status.COMPLETED,
            progress_stage="complete",
            progress_message="TikTok upload complete!",
            fix_progress=100,
        )
        cleanup_job_downloads(job_id)
        return

    username = resume.get("username") or job.tiktok_username or ""
    destinations = resume.get("destinations")
    snapshot = dict(job.workspace_snapshot or {})
    snapshot.pop("tiktok_pending_upload", None)
    snapshot.pop("pipeline_resume", None)
    job.workspace_snapshot = snapshot
    job.save(update_fields=["workspace_snapshot"])

    thread = threading.Thread(
        target=run_ai_fix_upload_job,
        args=(job_id, remaining_items[1:], username, job.user),
        kwargs={"destinations": destinations},
        daemon=True,
    )
    thread.start()


def start_ai_fix_upload_job(
    video_items: list[dict],
    username: str,
    user=None,
    destinations=None,
    *,
    workspace_snapshot: dict | None = None,
) -> UploadJob:
    import threading

    job = UploadJob.objects.create(
        user=user,
        total_videos=len(video_items),
        tiktok_username=username,
        workspace_snapshot=workspace_snapshot or {},
    )
    thread = threading.Thread(
        target=run_ai_fix_upload_job,
        args=(str(job.id), video_items, username, user, destinations),
        daemon=True,
    )
    thread.start()
    return job
