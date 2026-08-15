import json
from pathlib import Path
import logging
from functools import wraps
from urllib.parse import quote

import requests
from django.conf import settings
from django.contrib.auth import get_user_model, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.views import LoginView
from django.core.exceptions import ValidationError
from django.http import FileResponse, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from clipper.services.admin_plans import admin_required, grant_user_plan, list_users
from clipper.services.job_cancel import cancel_upload_job, job_is_active
from clipper.services.tiktok_cookies import (
    cookies_json_to_netscape as tiktok_cookies_json_to_netscape,
    create_cookie_extract_token as create_tiktok_cookie_extract_token,
    get_tiktok_cookies_status,
    pop_cookie_extract_user_id as pop_tiktok_cookie_extract_user_id,
    save_user_tiktok_cookies,
    validate_cookie_extract_token as validate_tiktok_cookie_extract_token,
    validate_netscape_cookies as validate_tiktok_netscape_cookies,
)
from clipper.services.limits import check_can_upload, get_upload_quota, is_admin_user
from clipper.services.youtube_cookies import (
    cookies_json_to_netscape,
    create_cookie_extract_token,
    get_youtube_cookies_status,
    pop_cookie_extract_user_id,
    save_user_youtube_cookies,
    save_youtube_cookies_file,
    validate_cookie_extract_token,
    validate_netscape_cookies,
)
from clipper.services.nowpayments import ALLOWED_COINS, NOWPaymentsError, get_available_currencies, verify_ipn_signature
from clipper.services.pipeline import start_ai_fix_upload_job, start_clip_upload_job
from clipper.services.twitch_preview_pipeline import (
    start_twitch_clip_upload_job,
    start_twitch_vod_preview_job,
)
from clipper.services.posting_preferences import (
    ai_features_enabled,
    get_posting_preferences,
    normalize_destinations,
    save_posting_preferences,
    user_has_any_upload_path,
    validate_destinations,
)
from clipper.services.tiktok_oauth import (
    TikTokOAuthError,
    build_redirect_uri as build_tiktok_redirect_uri,
    disconnect_tiktok,
    exchange_code_for_tokens,
    get_authorization_url as get_tiktok_authorization_url,
    get_tiktok_account,
    is_tiktok_connected,
    save_tokens,
)
from clipper.services.subscription import (
    create_subscription_payment,
    generate_qr_png,
    get_subscription_info,
    handle_ipn_payload,
    payment_to_dict,
    sync_payment_status,
)
from clipper.models import (
    PostingPreferences,
    ScheduledSource,
    SubscriptionPayment,
    UploadJob,
    UploadedVideo,
)
from clipper.services.schedule_runner import (
    check_user_schedules,
    recent_uploads,
)
from clipper.services.schedule_service import (
    add_source,
    list_sources,
    source_to_dict,
    update_source,
)
from clipper.services.ai_clip_check import analyze_videos
from clipper.services.ai_selective_fix import create_selective_preview, get_preview_payload, publish_preview
from clipper.services.channel_ai import analyze_channel_with_ai
from clipper.services.channel_analysis import build_channel_snapshot
from clipper.services.media_fetch import fetch_videos
from clipper.services.ollama_client import get_status as get_ollama_status
from clipper.services.ai_clip_score import score_fetched_videos
from clipper.services.ai_moment_finder import find_clip_moments
from clipper.services.processing_settings import (
    get_video_processing_settings,
    save_video_processing_settings,
)
from clipper.services.upload_settings import get_upload_settings, save_upload_settings
from clipper.services.youtube import (
    build_redirect_uri,
    clear_credentials,
    exchange_code_for_credentials,
    get_authorization_url,
    get_channel_info,
    get_credentials,
    has_youtube_analytics_scope,
    has_youtube_comment_scope,
    is_youtube_connected,
    pool_configured,
    save_credentials,
    user_has_comment_scope,
    user_has_analytics_scope,
)
from clipper.services import zernio_youtube as zernio
from clipper.services.zernio_youtube import get_connection
from clipper.services.youtube_oauth_pending import resolve_oauth_callback
from clipper.services.youtube_pool_auth import continue_pool_oauth_queue, projects_needing_oauth, start_pool_oauth
from clipper.services.youtube_upload import pool_credentials_linked, pool_credentials_total, user_has_upload_path

logger = logging.getLogger(__name__)


def _workspace_snapshot_from_body(body: dict) -> dict:
    workspace = body.get("workspace")
    if not isinstance(workspace, dict):
        return {}

    videos = workspace.get("videos") or []
    if not isinstance(videos, list):
        videos = []
    if len(videos) > 500:
        videos = videos[:500]

    selected_ids = workspace.get("selectedIds") or workspace.get("selected_ids") or []
    if not isinstance(selected_ids, list):
        selected_ids = []

    preview_clips = workspace.get("previewClips") or workspace.get("preview_clips") or []
    if not isinstance(preview_clips, list):
        preview_clips = []

    selected_preview_ids = workspace.get("selectedPreviewIds") or workspace.get("selected_preview_ids") or []
    if not isinstance(selected_preview_ids, list):
        selected_preview_ids = []

    fetch_input = str(workspace.get("fetchInput") or workspace.get("fetch_input") or "")[:500]
    last_fetch_summary = str(
        workspace.get("lastFetchSummary") or workspace.get("last_fetch_summary") or ""
    )[:500]

    active_videos = workspace.get("activeVideos") or workspace.get("active_videos") or []
    if not isinstance(active_videos, list):
        active_videos = []

    return {
        "mode": workspace.get("mode") or "grid",
        "videos": videos,
        "selectedIds": [str(item) for item in selected_ids[:500]],
        "previewClips": preview_clips[:200],
        "selectedPreviewIds": [str(item) for item in selected_preview_ids[:200]],
        "previewJobId": str(workspace.get("previewJobId") or workspace.get("preview_job_id") or ""),
        "currentUsername": str(workspace.get("currentUsername") or workspace.get("current_username") or "")[:255],
        "currentSource": str(workspace.get("currentSource") or workspace.get("current_source") or "tiktok")[:32],
        "fetchInput": fetch_input,
        "lastFetchSummary": last_fetch_summary,
        "activeVodId": str(workspace.get("activeVodId") or workspace.get("active_vod_id") or "")[:64],
        "activeVodUrl": str(workspace.get("activeVodUrl") or workspace.get("active_vod_url") or "")[:500],
        "jobVideoTitle": str(workspace.get("jobVideoTitle") or workspace.get("job_video_title") or "")[:500],
        "activeVideos": active_videos[:20],
    }


def _youtube_channel(user, session):
    creds = get_credentials(user, session)
    channel = get_channel_info(creds) if creds else None
    if not channel and zernio.has_zernio_account(user):
        channel = zernio.get_channel_info(user)
    return channel


def _redirect_zernio_connect(request):
    if not settings.ZERNIO_API_KEY:
        return None
    if zernio.has_zernio_account(request.user):
        return None
    try:
        profile_id = zernio.get_or_create_profile(request.user)
        callback_url = f"{settings.SITE_BASE_URL.rstrip('/')}/api/youtube/zernio-callback/"
        auth_url = zernio.get_connect_url(profile_id, callback_url)
        return redirect(auth_url)
    except zernio.ZernioError as exc:
        logger.warning("Zernio backup connect skipped for %s: %s", request.user.username, exc)
        return None


def api_login_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return JsonResponse({"error": "Authentication required."}, status=401)
        return view_func(request, *args, **kwargs)

    return wrapper


def admin_api_required(view_func):
    @api_login_required
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not is_admin_user(request.user):
            return JsonResponse({"error": "Admin access required."}, status=403)
        return view_func(request, *args, **kwargs)

    return wrapper


class AppLoginView(LoginView):
    template_name = "clipper/login.html"
    redirect_authenticated_user = True


@require_POST
def api_register(request):
    if not getattr(settings, "ALLOW_PUBLIC_REGISTRATION", True):
        return JsonResponse({"error": "Registration is disabled."}, status=403)

    try:
        body = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON body."}, status=400)

    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    password2 = body.get("password2") or ""

    if not username:
        return JsonResponse({"error": "Username is required."}, status=400)
    if len(username) < 3:
        return JsonResponse({"error": "Username must be at least 3 characters."}, status=400)
    if not password:
        return JsonResponse({"error": "Password is required."}, status=400)
    if password != password2:
        return JsonResponse({"error": "Passwords do not match."}, status=400)

    User = get_user_model()
    if User.objects.filter(username__iexact=username).exists():
        return JsonResponse({"error": "That username is already taken."}, status=400)

    try:
        validate_password(password, user=User(username=username))
    except ValidationError as exc:
        return JsonResponse({"error": " ".join(exc.messages)}, status=400)

    User.objects.create_user(username=username, password=password)
    handle = settings.TELEGRAM_CONTACT_HANDLE
    return JsonResponse(
        {
            "success": True,
            "message": f"Account '{username}' created. Sign in on the left.",
            "toast": (
                f"Account created! Message {handle} on Telegram with the "
                "Google/YouTube email for the channel you want to upload to."
            ),
            "redirect_url": settings.TELEGRAM_CONTACT_URL,
        }
    )


def privacy(request):
    return render(request, "clipper/privacy.html")


def terms(request):
    return render(request, "clipper/terms.html")


TIKTOK_SITE_VERIFICATION_FILENAME = "tiktokiNY9xldFvd4Sgtrl1DGPCWLt3WnLQEl2.txt"


@require_GET
def tiktok_site_verification(request):
    filepath = settings.BASE_DIR / TIKTOK_SITE_VERIFICATION_FILENAME
    content = filepath.read_text(encoding="utf-8")
    return HttpResponse(content, content_type="text/plain; charset=utf-8")


def home(request):
    return render(request, "clipper/home.html")


@login_required
def panel(request):
    connected = is_youtube_connected(request.user, request.session)
    channel = _youtube_channel(request.user, request.session)
    quota = get_upload_quota(request.user)
    subscription = get_subscription_info(request.user)
    comment_settings = get_upload_settings(request.user)
    video_processing = get_video_processing_settings(request.user)
    posting_prefs = get_posting_preferences(request.user)
    return render(
        request,
        "clipper/panel.html",
        {
            "youtube_connected": connected,
            "youtube_channel": channel,
            "tiktok_connected": is_tiktok_connected(request.user),
            "tiktok_account": get_tiktok_account(request.user),
            "posting_preferences": posting_prefs,
            "upload_quota": quota,
            "subscription": subscription,
            "is_app_admin": is_admin_user(request.user),
            "comment_settings": comment_settings,
            "video_processing": video_processing,
            "site_base_url": settings.SITE_BASE_URL,
        },
    )


@login_required
@require_POST
def app_logout(request):
    logout(request)
    return redirect("login")


@api_login_required
@require_POST
def api_fetch_videos(request):
    try:
        body = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON body."}, status=400)

    raw_input = body.get("input", "").strip()
    source = (body.get("source") or "auto").strip().lower()
    if not raw_input:
        return JsonResponse({"error": "Input is required."}, status=400)

    try:
        # Return the full fetched list immediately. Do not block on Ollama ranking —
        # Enable AI still applies at clip/upload time, not during channel fetch.
        data = fetch_videos(raw_input, source=source, user=request.user)
        return JsonResponse({"success": True, **data})
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    except Exception as exc:
        return JsonResponse({"error": f"Unexpected error: {exc}"}, status=500)


@api_login_required
@require_POST
def api_ai_score_videos(request):
    if not ai_features_enabled(request.user):
        return JsonResponse({"error": "AI features are turned off. Enable them in Controls."}, status=400)
    try:
        body = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON body."}, status=400)

    videos = body.get("videos") or []
    if not isinstance(videos, list) or not videos:
        return JsonResponse({"error": "videos list is required."}, status=400)

    ollama = get_ollama_status()
    if not ollama.get("connected"):
        return JsonResponse(
            {"error": ollama.get("error") or "Remote Ollama is not reachable."},
            status=503,
        )
    try:
        result = score_fetched_videos(request.user, videos)
        return JsonResponse({"success": True, **result, "ollama": ollama})
    except Exception as exc:
        logger.exception("AI score failed")
        return JsonResponse({"error": str(exc)}, status=500)


@api_login_required
@require_POST
def api_ai_find_moments(request):
    if not ai_features_enabled(request.user):
        return JsonResponse({"error": "AI features are turned off. Enable them in Controls."}, status=400)
    try:
        body = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON body."}, status=400)

    url = (body.get("url") or body.get("input") or "").strip()
    source = (body.get("source") or "auto").strip().lower()
    title = (body.get("title") or "").strip()
    if not url:
        return JsonResponse({"error": "url is required."}, status=400)

    ollama = get_ollama_status()
    if not ollama.get("connected"):
        return JsonResponse(
            {"error": ollama.get("error") or "Remote Ollama is not reachable."},
            status=503,
        )
    if not ollama.get("vision_ready"):
        return JsonResponse(
            {
                "error": (
                    f"Free vision model {ollama.get('vision_model')} is not available on Ollama. "
                    "Pull moondream locally (no paid/cloud models)."
                )
            },
            status=503,
        )

    try:
        result = find_clip_moments(url, source=source, title=title, user=request.user)
        return JsonResponse({"success": True, **result, "ollama": ollama})
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    except Exception as exc:
        logger.exception("Moment finder failed")
        return JsonResponse({"error": str(exc)}, status=500)


@api_login_required
@require_GET
def api_ollama_status(request):
    return JsonResponse(
        {
            "success": True,
            "ollama": get_ollama_status(),
            "youtube_cookies": get_youtube_cookies_status(request.user),
        }
    )


@api_login_required
@require_GET
def api_youtube_cookies_status(request):
    return JsonResponse(
        {"success": True, "youtube_cookies": get_youtube_cookies_status(request.user)}
    )


@api_login_required
@require_GET
def api_youtube_cookies_extract_start(request):
    token = create_cookie_extract_token(request.user.pk)
    site_base = settings.SITE_BASE_URL.rstrip("/")
    return JsonResponse(
        {
            "success": True,
            "token": token,
            "site_base": site_base,
            "extract_url": f"{site_base}/youtube-cookies/extract/?token={token}",
            "youtube_url": (
                f"https://www.youtube.com/?tcliper_sync={token}"
                f"&tcliper_site={quote(site_base, safe='')}"
            ),
            "complete_url": f"{site_base}/youtube-cookies/complete/?token={token}",
        }
    )


@csrf_exempt
@require_POST
def api_youtube_cookies_extract_finish(request):
    try:
        body = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON body."}, status=400)

    token = str(body.get("token") or "").strip()
    user_id = validate_cookie_extract_token(token)
    if not user_id:
        return JsonResponse({"error": "Cookie extraction session expired. Start again."}, status=400)

    from django.contrib.auth import get_user_model

    user = get_user_model().objects.filter(pk=user_id).first()
    if not user:
        return JsonResponse({"error": "User not found."}, status=404)

    cookies_netscape = str(body.get("cookies") or "").strip()
    cookies_json = body.get("cookies_json")
    if not cookies_netscape and isinstance(cookies_json, list):
        cookies_netscape = cookies_json_to_netscape(cookies_json)

    if not cookies_netscape:
        return JsonResponse({"error": "No cookies were provided."}, status=400)

    try:
        validate_netscape_cookies(cookies_netscape)
        status = save_user_youtube_cookies(user, cookies_netscape)
        pop_cookie_extract_user_id(token)
        return JsonResponse({"success": True, "youtube_cookies": status})
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)


@login_required
@require_GET
def youtube_cookies_extract(request):
    token = (request.GET.get("token") or "").strip()
    if not token or not validate_cookie_extract_token(token):
        return render(
            request,
            "clipper/youtube_cookies_extract.html",
            {
                "error": "Cookie extraction session expired. Go back to AI Check and start again.",
                "token": "",
                "site_base": settings.SITE_BASE_URL.rstrip("/"),
            },
            status=400,
        )
    return render(
        request,
        "clipper/youtube_cookies_extract.html",
        {
            "error": "",
            "token": token,
            "site_base": settings.SITE_BASE_URL.rstrip("/"),
        },
    )


@login_required
@require_GET
def youtube_cookies_complete(request):
    ok = request.GET.get("ok") == "1"
    token = (request.GET.get("token") or "").strip()
    return render(
        request,
        "clipper/youtube_cookies_complete.html",
        {
            "ok": ok,
            "token": token,
            "site_base": settings.SITE_BASE_URL.rstrip("/"),
        },
    )


@api_login_required
@require_POST
def api_youtube_cookies_save(request):
    try:
        body = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON body."}, status=400)

    cookies = str(body.get("cookies") or "").strip()
    if not cookies:
        return JsonResponse({"error": "Cookies are required."}, status=400)
    try:
        status = save_user_youtube_cookies(request.user, cookies)
        return JsonResponse({"success": True, "youtube_cookies": status})
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)


@admin_api_required
@require_POST
def api_admin_youtube_cookies(request):
    uploaded = request.FILES.get("cookies")
    if not uploaded:
        return JsonResponse({"error": "Upload a cookies.txt file."}, status=400)
    try:
        status = save_youtube_cookies_file(uploaded)
        return JsonResponse({"success": True, "youtube_cookies": status})
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    except OSError as exc:
        return JsonResponse({"error": f"Could not save cookies file: {exc}"}, status=500)


@api_login_required
@require_GET
def api_channel_help_snapshot(request):
    snapshot = build_channel_snapshot(request.user, request.session)
    if not snapshot.get("connected"):
        return JsonResponse({"error": snapshot.get("error") or "Channel not connected."}, status=400)
    return JsonResponse({"success": True, "snapshot": snapshot})


@api_login_required
@require_POST
def api_channel_help_analyze(request):
    if not ai_features_enabled(request.user):
        return JsonResponse({"error": "AI features are turned off. Enable them in Controls."}, status=400)
    if not user_has_any_upload_path(request.user):
        return JsonResponse({"error": "Link YouTube or TikTok before scheduling uploads."}, status=400)

    ollama = get_ollama_status()
    if not ollama.get("connected"):
        return JsonResponse(
            {"error": ollama.get("error") or "Remote Ollama is not reachable."},
            status=503,
        )

    try:
        result = analyze_channel_with_ai(request.user, request.session)
        return JsonResponse({"success": True, **result, "ollama": ollama})
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    except Exception as exc:
        logger.exception("Channel AI analysis failed")
        return JsonResponse({"error": str(exc)}, status=500)


@api_login_required
@require_POST
def api_channel_help_ops(request):
    if not ai_features_enabled(request.user):
        return JsonResponse({"error": "AI features are turned off. Enable them in Controls."}, status=400)
    if not user_has_any_upload_path(request.user):
        return JsonResponse({"error": "Link YouTube or TikTok before using channel ops."}, status=400)

    ollama = get_ollama_status()
    if not ollama.get("connected"):
        return JsonResponse(
            {"error": ollama.get("error") or "Remote Ollama is not reachable."},
            status=503,
        )

    from clipper.services.channel_ops import build_channel_ops

    try:
        snapshot = build_channel_snapshot(request.user, request.session, include_trending=True)
        if not snapshot.get("connected"):
            return JsonResponse({"error": snapshot.get("error") or "Channel not connected."}, status=400)
        ops = build_channel_ops(request.user, snapshot)
        return JsonResponse({"success": True, "channel_ops": ops, "snapshot": snapshot, "ollama": ollama})
    except Exception as exc:
        logger.exception("Channel ops failed")
        return JsonResponse({"error": str(exc)}, status=500)


@api_login_required
@require_POST
def api_ai_check(request):
    try:
        body = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON body."}, status=400)

    videos = body.get("videos") or []
    if not videos:
        return JsonResponse({"error": "Select at least one video to check."}, status=400)
    if not ai_features_enabled(request.user):
        return JsonResponse({"error": "AI features are turned off. Enable them in Controls."}, status=400)
    if len(videos) > 5:
        return JsonResponse({"error": "You can AI-check up to 5 videos at a time."}, status=400)

    ollama = get_ollama_status()
    if not ollama.get("connected"):
        return JsonResponse(
            {"error": ollama.get("error") or "Remote Ollama is not reachable."},
            status=503,
        )
    if not ollama.get("vision_ready"):
        return JsonResponse(
            {"error": f"Vision model {ollama.get('vision_model')} is not available on remote Ollama."},
            status=503,
        )

    try:
        results = analyze_videos(videos, user=request.user)
        return JsonResponse({"success": True, "results": results, "ollama": ollama})
    except Exception as exc:
        logger.exception("AI check batch failed")
        return JsonResponse({"error": str(exc)}, status=500)


@api_login_required
@require_POST
def api_ai_check_fix_upload(request):
    try:
        body = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON body."}, status=400)

    if not user_has_any_upload_path(request.user):
        return JsonResponse({"error": "Link YouTube or TikTok before uploading."}, status=400)

    videos = body.get("videos") or []
    if not videos:
        return JsonResponse({"error": "Select at least one video to fix and upload."}, status=400)
    if not ai_features_enabled(request.user):
        return JsonResponse({"error": "AI features are turned off. Enable them in Controls."}, status=400)
    if len(videos) > 3:
        return JsonResponse({"error": "You can AI-fix up to 3 videos at a time."}, status=400)

    destinations = normalize_destinations(request.user, body.get("destinations"))
    try:
        validate_destinations(request.user, destinations)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    ollama = get_ollama_status()
    if not ollama.get("connected"):
        return JsonResponse(
            {"error": ollama.get("error") or "Remote Ollama is not reachable."},
            status=503,
        )

    allowed, limit_message = check_can_upload(request.user, len(videos))
    if not allowed:
        return JsonResponse({"error": limit_message}, status=400)

    username = body.get("username", "") or "ai-fix"
    job = start_ai_fix_upload_job(
        videos,
        username,
        user=request.user,
        destinations=destinations,
        workspace_snapshot=_workspace_snapshot_from_body(body),
    )
    return JsonResponse(
        {
            "success": True,
            "job_id": str(job.id),
            "message": f"AI is fixing and uploading {len(videos)} video(s).",
        }
    )


@api_login_required
@require_POST
def api_ai_check_preview_fix(request):
    if not ai_features_enabled(request.user):
        return JsonResponse({"error": "AI features are turned off. Enable them in Controls."}, status=400)
    try:
        body = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON body."}, status=400)

    video = body.get("video") or {}
    check_result = body.get("check_result") or {}
    selected_issue_ids = body.get("selected_issue_ids") or []
    if not video.get("id") or not video.get("url"):
        return JsonResponse({"error": "Video data is required."}, status=400)
    if not selected_issue_ids:
        return JsonResponse({"error": "Select at least one issue to fix."}, status=400)

    ollama = get_ollama_status()
    if not ollama.get("connected"):
        return JsonResponse(
            {"error": ollama.get("error") or "Remote Ollama is not reachable."},
            status=503,
        )

    try:
        preview = create_selective_preview(
            video,
            request.user,
            check_result=check_result,
            selected_issue_ids=selected_issue_ids,
            destinations=normalize_destinations(request.user, body.get("destinations")),
        )
        return JsonResponse({"success": True, **preview})
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    except Exception as exc:
        logger.exception("AI selective preview failed")
        return JsonResponse({"error": str(exc)}, status=500)


@api_login_required
@require_GET
def api_ai_check_preview_video(request, token: str):
    payload = get_preview_payload(request.user, token)
    if not payload:
        return JsonResponse({"error": "Preview not found or expired."}, status=404)

    filepath = payload.get("filepath")
    try:
        return FileResponse(open(filepath, "rb"), content_type="video/mp4")
    except OSError:
        return JsonResponse({"error": "Preview file missing."}, status=404)


@api_login_required
@require_POST
def api_ai_check_publish_preview(request):
    try:
        body = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON body."}, status=400)

    if not user_has_any_upload_path(request.user):
        return JsonResponse({"error": "Link YouTube or TikTok before publishing."}, status=400)

    token = (body.get("preview_token") or "").strip()
    if not token:
        return JsonResponse({"error": "Preview token is required."}, status=400)

    destinations = normalize_destinations(request.user, body.get("destinations"))
    try:
        validate_destinations(request.user, destinations)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    allowed, limit_message = check_can_upload(request.user, 1)
    if not allowed:
        return JsonResponse({"error": limit_message}, status=400)

    try:
        result = publish_preview(
            request.user,
            token,
            title=(body.get("title") or "").strip(),
            description=(body.get("description") or "").strip(),
            destinations=destinations,
        )
        return JsonResponse(
            {
                "success": True,
                "video_id": result.get("video_id"),
                "url": result.get("url"),
                "youtube": result.get("youtube"),
                "tiktok": result.get("tiktok"),
                "message": "Fixed clip published.",
            }
        )
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    except Exception as exc:
        logger.exception("Publish preview failed")
        return JsonResponse({"error": str(exc)}, status=500)


@api_login_required
@require_GET
def api_upload_settings(request):
    return JsonResponse({"success": True, "settings": get_upload_settings(request.user)})


@api_login_required
@require_POST
def api_upload_settings_save(request):
    try:
        body = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON body."}, status=400)

    if not user_has_upload_path(request.user):
        return JsonResponse(
            {"error": "Link your YouTube channel before saving comment settings."},
            status=400,
        )

    try:
        settings_data = save_upload_settings(
            request.user,
            enabled=bool(body.get("enabled")),
            comment_type=(body.get("comment_type") or "subscribe").strip(),
            custom_text=(body.get("custom_text") or body.get("comment_text") or "").strip(),
            comment_text=(body.get("comment_text") or body.get("custom_text") or "").strip(),
        )
        return JsonResponse({"success": True, "settings": settings_data})
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)


@api_login_required
@require_GET
def api_video_processing_settings(request):
    return JsonResponse(
        {"success": True, "settings": get_video_processing_settings(request.user)}
    )


@api_login_required
@require_POST
def api_video_processing_settings_save(request):
    try:
        body = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON body."}, status=400)

    if not user_has_upload_path(request.user):
        return JsonResponse(
            {"error": "Link your YouTube channel before saving video settings."},
            status=400,
        )

    try:
        settings_data = save_video_processing_settings(
            request.user,
            enabled=bool(body.get("enabled", True)),
            subscribe_overlay=bool(body.get("subscribe_overlay", True)),
            audio_replace=bool(body.get("audio_replace", False)),
            content_safety=bool(body.get("content_safety", True)),
            audio_mood=(body.get("audio_mood") or "upbeat").strip(),
        )
        return JsonResponse({"success": True, "settings": settings_data})
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)


@api_login_required
@require_POST
def api_clip_upload(request):
    try:
        body = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON body."}, status=400)

    creds = get_credentials(request.user, request.session)
    destinations = normalize_destinations(request.user, body.get("destinations"))
    try:
        validate_destinations(request.user, destinations)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    videos = body.get("videos", [])
    username = body.get("username", "")

    if not videos:
        return JsonResponse({"error": "Select at least one video."}, status=400)

    use_ai = ai_features_enabled(request.user)
    if use_ai:
        ollama = get_ollama_status()
        if not ollama.get("connected"):
            return JsonResponse(
                {"error": ollama.get("error") or "Remote AI (Ollama) must be online to clip and upload."},
                status=503,
            )
        if not ollama.get("vision_ready"):
            return JsonResponse(
                {"error": f"Vision model {ollama.get('vision_model')} is not available on remote Ollama."},
                status=503,
            )
    else:
        ollama = None

    allowed, limit_message = check_can_upload(request.user, len(videos))
    if not allowed:
        return JsonResponse({"error": limit_message}, status=400)

    credentials_json = ""
    connection = zernio.get_connection(request.user)
    if connection and connection.credentials_json:
        credentials_json = connection.credentials_json
    elif creds:
        credentials_json = creds.to_json()

    job = start_clip_upload_job(
        videos,
        username,
        credentials_json,
        user=request.user,
        destinations=destinations,
        use_ai=use_ai,
        workspace_snapshot=_workspace_snapshot_from_body(body),
    )
    return JsonResponse(
        {
            "success": True,
            "job_id": str(job.id),
            "message": (
                f"Started AI check, fix, and upload for {len(videos)} video(s)."
                if use_ai
                else f"Started clip and upload for {len(videos)} video(s)."
            ),
            "ai_enabled": use_ai,
        }
    )


@api_login_required
@require_POST
def api_twitch_vod_clips(request):
    try:
        body = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON body."}, status=400)

    if not ai_features_enabled(request.user):
        return JsonResponse({"error": "AI features are turned off. Enable them in Controls."}, status=400)

    ollama = get_ollama_status()
    if not ollama.get("connected"):
        return JsonResponse(
            {"error": ollama.get("error") or "Remote Ollama must be online for Twitch VOD clipping."},
            status=503,
        )

    vod = body.get("vod") or {}
    if not vod.get("url") and not vod.get("id"):
        return JsonResponse({"error": "A Twitch VOD is required."}, status=400)
    if (vod.get("source") or "").lower() not in {"", "twitch"}:
        return JsonResponse({"error": "Only Twitch VODs are supported for AI clipping."}, status=400)

    twitch_kind = (vod.get("twitch_kind") or "vod").lower()
    if twitch_kind in {"clip", "live"}:
        return JsonResponse(
            {"error": "AI clip generation works on full VODs and long highlights — not Twitch clips or live streams."},
            status=400,
        )
    duration = int(vod.get("duration") or 0)
    if duration and duration < 120:
        return JsonResponse(
            {"error": "Select a longer VOD or highlight (at least 2 minutes) for AI clipping."},
            status=400,
        )

    username = body.get("username") or vod.get("user_login") or vod.get("user_name") or "twitch"
    job = start_twitch_vod_preview_job(
        vod,
        username,
        user=request.user,
        workspace_snapshot=_workspace_snapshot_from_body(body),
    )
    return JsonResponse(
        {
            "success": True,
            "job_id": str(job.id),
            "message": "AI is analyzing the VOD, chat, and cutting as many clips as it can find...",
        }
    )


@api_login_required
@require_POST
def api_twitch_upload_clips(request):
    try:
        body = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON body."}, status=400)

    job_id = body.get("job_id")
    if not job_id:
        return JsonResponse({"error": "job_id is required."}, status=400)

    try:
        job = UploadJob.objects.get(id=job_id, user=request.user)
    except UploadJob.DoesNotExist:
        return JsonResponse({"error": "Job not found."}, status=404)

    if job.progress_stage != "preview_ready":
        return JsonResponse({"error": "This job does not have preview clips ready to upload."}, status=400)

    clip_ids = body.get("clip_ids") or []
    if not isinstance(clip_ids, list) or not clip_ids:
        return JsonResponse({"error": "Select at least one clip to upload."}, status=400)

    try:
        clip_ids = [int(clip_id) for clip_id in clip_ids]
    except (TypeError, ValueError):
        return JsonResponse({"error": "Invalid clip selection."}, status=400)

    preview_qs = job.videos.filter(status="preview", id__in=clip_ids)
    preview_count = preview_qs.count()
    if preview_count != len(set(clip_ids)):
        return JsonResponse({"error": "One or more selected clips were not found."}, status=400)

    destinations = normalize_destinations(request.user, body.get("destinations"))
    try:
        validate_destinations(request.user, destinations)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    allowed, limit_message = check_can_upload(request.user, preview_count)
    if not allowed:
        return JsonResponse({"error": limit_message}, status=400)

    start_twitch_clip_upload_job(str(job.id), request.user, destinations=destinations, clip_ids=clip_ids)
    return JsonResponse(
        {
            "success": True,
            "job_id": str(job.id),
            "message": f"Uploading {preview_count} clip(s)...",
        }
    )


@api_login_required
@require_GET
def api_job_status(request, job_id):
    try:
        job = UploadJob.objects.get(id=job_id, user=request.user)
    except UploadJob.DoesNotExist:
        return JsonResponse({"error": "Job not found."}, status=404)

    return JsonResponse(_build_job_status_payload(job))


@api_login_required
@require_POST
def api_job_cancel(request, job_id):
    try:
        job = UploadJob.objects.get(id=job_id, user=request.user)
    except UploadJob.DoesNotExist:
        return JsonResponse({"error": "Job not found."}, status=404)

    if job.progress_stage == "cancelled" or job.cancel_requested:
        return JsonResponse({"success": True, "message": "Job already cancelled."})

    if not job_is_active(job):
        return JsonResponse({"error": "This job is no longer running."}, status=400)

    cancel_upload_job(job)
    return JsonResponse({"success": True, "message": "Job cancelled."})


def _build_job_status_payload(job: UploadJob) -> dict:
    videos = list(
        job.videos.values(
            "id",
            "title",
            "status",
            "youtube_url",
            "tiktok_url",
            "error_message",
            "tiktok_id",
        )
    )

    preview_clips = []
    for video in videos:
        if video.get("status") != "preview":
            continue
        meta = {}
        try:
            meta = json.loads(video.get("error_message") or "{}")
        except json.JSONDecodeError:
            meta = {}
        preview_clips.append(
            {
                "id": video.get("id"),
                "title": video.get("title"),
                "category": meta.get("category") or "hype",
                "reason": meta.get("reason") or "",
                "start_sec": meta.get("start_sec"),
                "end_sec": meta.get("end_sec"),
                "preview_url": f"/api/job/{job.id}/preview/{video.get('id')}/",
                "duration": max(
                    0,
                    int(float(meta.get("end_sec") or 0) - float(meta.get("start_sec") or 0)),
                ),
            }
        )

    return {
        "job_id": str(job.id),
        "status": job.status,
        "tiktok_username": job.tiktok_username,
        "total_videos": job.total_videos,
        "completed_videos": job.completed_videos,
        "progress_percent": job.progress_percent,
        "current_video_title": job.current_video_title,
        "progress_message": job.progress_message,
        "progress_stage": job.progress_stage,
        "fix_progress": job.fix_progress,
        "error_message": job.error_message,
        "videos": videos,
        "preview_clips": preview_clips,
        "workspace_snapshot": job.workspace_snapshot or {},
        "tiktok_pending_upload": (job.workspace_snapshot or {}).get("tiktok_pending_upload") or None,
    }


@api_login_required
@require_GET
def api_active_job(request):
    from datetime import timedelta

    from django.utils import timezone

    from clipper.services.youtube_quota import format_quota_wait_message, get_quota_resume_at

    running = UploadJob.objects.filter(
        user=request.user,
        status__in=[
            UploadJob.Status.PENDING,
            UploadJob.Status.DOWNLOADING,
            UploadJob.Status.UPLOADING,
        ],
    ).order_by("-updated_at").first()

    # Rate-limit failures often leave jobs stuck in "uploading".
    # Close them so they stop restoring that channel on every dashboard load.
    if running and running.status == UploadJob.Status.UPLOADING:
        message = f"{running.progress_message or ''}".lower()
        if "rate-limited" in message or "429" in message:
            running.status = UploadJob.Status.FAILED
            running.save(update_fields=["status", "updated_at"])
            running = None

    # Auto-fail jobs that have made no progress for too long (e.g. hung yt-dlp at 15%).
    if running and running.updated_at and (timezone.now() - running.updated_at) > timedelta(minutes=8):
        stage = (running.progress_stage or "").lower()
        msg = (running.progress_message or "").lower()
        if stage in {"downloading", "enhancing", "uploading", ""} or "downloading" in msg:
            resume = get_quota_resume_at(request.user)
            fail_msg = (
                format_quota_wait_message(resume)
                if resume
                else "Upload stalled while downloading. Cancelled — try again with fewer clips."
            )
            running.status = UploadJob.Status.FAILED
            running.progress_stage = "failed"
            running.progress_message = fail_msg
            running.error_message = fail_msg
            running.cancel_requested = True
            running.save(
                update_fields=[
                    "status",
                    "progress_stage",
                    "progress_message",
                    "error_message",
                    "cancel_requested",
                    "updated_at",
                ]
            )
            running = None

    job = running
    if not job:
        preview_job = (
            UploadJob.objects.filter(
                user=request.user,
                status=UploadJob.Status.COMPLETED,
                progress_stage="preview_ready",
            )
            .order_by("-updated_at")
            .first()
        )
        if preview_job and preview_job.videos.filter(status="preview").exists():
            job = preview_job

    if not job:
        return JsonResponse({"success": True, "active": False})

    payload = _build_job_status_payload(job)
    if not payload.get("workspace_snapshot") and job.tiktok_username:
        payload["workspace_snapshot"] = {
            "mode": "grid",
            "videos": [],
            "selectedIds": [],
            "currentUsername": job.tiktok_username,
            "currentSource": "twitch",
            "fetchInput": job.tiktok_username,
            "lastFetchSummary": "",
            "jobVideoTitle": job.current_video_title or "",
        }
    payload["success"] = True
    payload["active"] = True
    return JsonResponse(payload)


@api_login_required
@require_GET
def api_job_clip_preview(request, job_id, video_id):
    try:
        job = UploadJob.objects.get(id=job_id, user=request.user)
        record = job.videos.get(id=video_id, status="preview")
    except (UploadJob.DoesNotExist, UploadedVideo.DoesNotExist):
        return JsonResponse({"error": "Preview not found."}, status=404)

    try:
        meta = json.loads(record.error_message or "{}")
    except json.JSONDecodeError:
        meta = {}
    clip_name = meta.get("preview_file") or "clip_001.mp4"
    clip_path = Path(settings.DOWNLOADS_DIR) / str(job.id) / "clips" / clip_name
    if not clip_path.exists():
        return JsonResponse({"error": "Preview file missing."}, status=404)
    return FileResponse(open(clip_path, "rb"), content_type="video/mp4")


@api_login_required
@require_GET
def api_proxy_image(request):
    url = request.GET.get("url", "").strip()
    if not url or not url.startswith("https://"):
        return HttpResponse(status=400)

    try:
        resp = requests.get(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Referer": "https://www.tiktok.com/",
            },
            timeout=12,
        )
        resp.raise_for_status()
        content_type = resp.headers.get("Content-Type", "image/jpeg")
        if not content_type.startswith("image/"):
            return HttpResponse(status=502)
        return HttpResponse(resp.content, content_type=content_type)
    except requests.RequestException:
        return HttpResponse(status=502)


@api_login_required
@require_GET
def api_youtube_status(request):
    connected = is_youtube_connected(request.user, request.session)
    channel = _youtube_channel(request.user, request.session)
    creds = get_credentials(request.user, request.session)
    comments_ready = user_has_comment_scope(request.user)
    analytics_ready = user_has_analytics_scope(request.user)
    linked = pool_credentials_linked(request.user)
    total = pool_credentials_total()
    missing = projects_needing_oauth(request.user)
    return JsonResponse(
        {
            "connected": connected,
            "channel": channel,
            "zernio_connected": zernio.has_zernio_account(request.user),
            "comments_ready": comments_ready,
            "needs_comment_relink": bool(connected and creds and not comments_ready),
            "analytics_ready": analytics_ready,
            "needs_analytics_relink": bool(connected and creds and not analytics_ready),
            "pool_linked": linked,
            "pool_total": total,
            "pool_complete": connected and not missing,
            "pool_missing": missing,
        }
    )


@login_required
@require_GET
def api_youtube_auth(request):
    creds = get_credentials(request.user, request.session)
    upgrade_comments = request.GET.get("upgrade_comments") == "1"
    upgrade_analytics = request.GET.get("upgrade_analytics") == "1"
    missing_comment_scope = bool(creds and not has_youtube_comment_scope(creds))
    missing_analytics_scope = bool(creds and not has_youtube_analytics_scope(creds))

    missing_pool = projects_needing_oauth(request.user)

    if is_youtube_connected(request.user, request.session):
        if (
            upgrade_comments
            or upgrade_analytics
            or missing_comment_scope
            or missing_analytics_scope
            or missing_pool
        ):
            if missing_pool and not (
                upgrade_comments or upgrade_analytics or missing_comment_scope or missing_analytics_scope
            ):
                request.session["youtube_oauth_include_analytics"] = False
            else:
                request.session["youtube_oauth_include_analytics"] = (
                    upgrade_analytics or missing_analytics_scope
                )
            request.session.pop("youtube_oauth_pool_index", None)
            request.session.pop("youtube_oauth_pool_queue", None)
            request.session.modified = True
            return start_pool_oauth(request)

        zernio_redirect = _redirect_zernio_connect(request)
        if zernio_redirect:
            return zernio_redirect
        linked = pool_credentials_linked(request.user)
        total = pool_credentials_total()
        return redirect(f"/dashboard/?youtube_connected=1&pool_linked={linked}&pool_total={total}")

    if not pool_configured():
        zernio_redirect = _redirect_zernio_connect(request)
        if zernio_redirect:
            return zernio_redirect
        return redirect(
            "/dashboard/?youtube_error="
            + quote("YouTube OAuth project pool is not configured.")
        )

    request.session.pop("youtube_oauth_pool_index", None)
    request.session.pop("youtube_oauth_pool_queue", None)
    request.session["youtube_oauth_include_analytics"] = False
    request.session.modified = True
    return start_pool_oauth(request)


@login_required
@require_GET
def api_youtube_callback(request):
    if request.GET.get("error"):
        message = request.GET.get("error_description") or request.GET.get("error")
        return redirect("/dashboard/?youtube_error=" + quote(message))

    code = request.GET.get("code")
    if not code:
        return redirect(
            "/dashboard/?youtube_error="
            + quote("No authorization code received from Google.")
        )

    state = request.GET.get("state")
    oauth_ctx = resolve_oauth_callback(request.user, state or "", request.session)
    saved_state = oauth_ctx.get("saved_state")
    code_verifier = oauth_ctx.get("code_verifier")
    redirect_uri = oauth_ctx.get("redirect_uri") or build_redirect_uri(request)
    project_id = oauth_ctx.get("project_id")
    include_analytics = bool(oauth_ctx.get("include_analytics", False))
    pool_queue = oauth_ctx.get("pool_queue") or []

    request.session.pop("youtube_oauth_state", None)
    request.session.pop("youtube_oauth_code_verifier", None)
    request.session.pop("youtube_redirect_uri", None)
    request.session.pop("youtube_oauth_project_id", None)
    request.session.pop("youtube_oauth_include_analytics", None)
    if pool_queue:
        request.session["youtube_oauth_pool_queue"] = pool_queue
        request.session.modified = True

    if not saved_state or state != saved_state:
        return redirect(
            "/dashboard/?youtube_error="
            + quote("OAuth state mismatch. Please click Link YouTube again.")
        )

    if not project_id or not code_verifier:
        return redirect(
            "/dashboard/?youtube_error="
            + quote("OAuth session expired. Please click Link YouTube again.")
        )

    try:
        creds = exchange_code_for_credentials(
            project_id,
            code,
            redirect_uri,
            state=state,
            code_verifier=code_verifier,
            include_analytics=include_analytics,
        )
        save_credentials(request.user, request.session, creds, project_id=project_id)
    except Exception as exc:
        return redirect("/dashboard/?youtube_error=" + quote(str(exc)))

    chained = continue_pool_oauth_queue(request)
    if chained:
        return chained

    zernio_redirect = _redirect_zernio_connect(request)
    if zernio_redirect:
        return zernio_redirect

    linked = len((get_connection(request.user).project_credentials or {}))
    return redirect(f"/dashboard/?youtube_connected=1&pool_linked={linked}")


@login_required
@require_GET
def api_youtube_zernio_callback(request):
    if request.GET.get("error"):
        message = request.GET.get("error_description") or request.GET.get("error")
        return redirect("/dashboard/?youtube_error=" + quote(message))

    connected = request.GET.get("connected")
    account_id = request.GET.get("accountId")
    profile_id = request.GET.get("profileId")
    username = request.GET.get("username", "")

    if connected == "youtube" and account_id and profile_id:
        try:
            zernio.save_zernio_account(
                request.user,
                profile_id=profile_id,
                account_id=account_id,
                username=username,
            )
        except zernio.ZernioError as exc:
            return redirect("/dashboard/?youtube_error=" + quote(str(exc)))
        return redirect("/dashboard/?youtube_connected=1")

    return redirect(
        "/dashboard/?youtube_error="
        + quote("Could not connect YouTube backup channel.")
    )


@api_login_required
@require_POST
def api_youtube_disconnect(request):
    clear_credentials(request.user, request.session)
    return JsonResponse({"success": True, "message": "YouTube channel unlinked."})


@api_login_required
@require_GET
def api_tiktok_status(request):
    from clipper.services.tiktok_oauth import is_configured

    account = get_tiktok_account(request.user)
    cookies_status = get_tiktok_cookies_status(request.user)
    return JsonResponse(
        {
            "connected": is_tiktok_connected(request.user),
            "account": account,
            "configured": is_configured(),
            "browser_session": cookies_status,
            "posting_method": (account or {}).get("posting_method") or "",
        }
    )


@login_required
@require_GET
def api_tiktok_auth(request):
    try:
        return redirect(get_tiktok_authorization_url(request))
    except TikTokOAuthError as exc:
        return redirect("/dashboard/?tiktok_error=" + quote(str(exc)))


@login_required
@require_GET
def api_tiktok_callback(request):
    if request.GET.get("error"):
        message = request.GET.get("error_description") or request.GET.get("error")
        return redirect("/dashboard/?tiktok_error=" + quote(message))

    code = request.GET.get("code")
    if not code:
        return redirect(
            "/dashboard/?tiktok_error="
            + quote("No authorization code received from TikTok.")
        )

    state = request.GET.get("state")
    saved_state = request.session.pop("tiktok_oauth_state", None)
    if not saved_state or state != saved_state:
        return redirect(
            "/dashboard/?tiktok_error="
            + quote("OAuth state mismatch. Please click Link TikTok again.")
        )

    try:
        token_data = exchange_code_for_tokens(code, build_tiktok_redirect_uri(request))
        save_tokens(request.user, token_data)
    except TikTokOAuthError as exc:
        return redirect("/dashboard/?tiktok_error=" + quote(str(exc)))

    return redirect("/dashboard/?tiktok_connected=1")


@api_login_required
@require_POST
def api_tiktok_disconnect(request):
    disconnect_tiktok(request.user)
    return JsonResponse({"success": True, "message": "TikTok account unlinked."})


@api_login_required
@require_GET
def api_tiktok_cookies_status(request):
    return JsonResponse(
        {"success": True, "tiktok_cookies": get_tiktok_cookies_status(request.user)}
    )


@api_login_required
@require_GET
def api_tiktok_cookies_extract_start(request):
    token = create_tiktok_cookie_extract_token(request.user.pk)
    site_base = settings.SITE_BASE_URL.rstrip("/")
    return JsonResponse(
        {
            "success": True,
            "token": token,
            "site_base": site_base,
            "extract_url": f"{site_base}/tiktok-cookies/extract/?token={token}",
            "tiktok_url": (
                f"https://www.tiktok.com/?tcliper_tiktok_sync={token}"
                f"&tcliper_site={quote(site_base, safe='')}"
            ),
            "complete_url": f"{site_base}/tiktok-cookies/complete/?token={token}",
        }
    )


@csrf_exempt
@require_POST
def api_tiktok_cookies_extract_finish(request):
    try:
        body = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON body."}, status=400)

    token = str(body.get("token") or "").strip()
    user_id = validate_tiktok_cookie_extract_token(token)
    if not user_id:
        return JsonResponse({"error": "Cookie extraction session expired. Start again."}, status=400)

    from django.contrib.auth import get_user_model

    user = get_user_model().objects.filter(pk=user_id).first()
    if not user:
        return JsonResponse({"error": "User not found."}, status=404)

    cookies_netscape = str(body.get("cookies") or "").strip()
    cookies_json = body.get("cookies_json")
    if not cookies_netscape and isinstance(cookies_json, list):
        cookies_netscape = tiktok_cookies_json_to_netscape(cookies_json)

    if not cookies_netscape:
        return JsonResponse({"error": "No cookies were provided."}, status=400)

    try:
        validate_tiktok_netscape_cookies(cookies_netscape)
        status = save_user_tiktok_cookies(
            user,
            cookies_netscape,
            username=str(body.get("username") or "").strip().lstrip("@"),
            display_name=str(body.get("display_name") or "").strip(),
            cookies_json=cookies_json if isinstance(cookies_json, list) else None,
        )
        pop_tiktok_cookie_extract_user_id(token)
        return JsonResponse({"success": True, "tiktok_cookies": status})
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)


@require_GET
def api_tiktok_client_upload_video(request, token):
    from clipper.services.tiktok_client_upload import get_pending_upload

    pending = get_pending_upload(token)
    if not pending:
        return JsonResponse({"error": "Upload session expired."}, status=404)

    clip_path = Path(pending["filepath"])
    if not clip_path.is_file():
        return JsonResponse({"error": "Clip file missing on server."}, status=404)

    return FileResponse(open(clip_path, "rb"), content_type="video/mp4", filename=clip_path.name)


@csrf_exempt
@require_POST
def api_tiktok_client_upload_complete(request, token):
    from clipper.services.tiktok_client_upload import complete_tiktok_client_upload

    try:
        result = complete_tiktok_client_upload(token)
        return JsonResponse(result)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)


@login_required
@require_GET
def tiktok_cookies_extract(request):
    token = (request.GET.get("token") or "").strip()
    if not token or not validate_tiktok_cookie_extract_token(token):
        return render(
            request,
            "clipper/tiktok_cookies_extract.html",
            {"token": "", "invalid": True},
        )
    return render(
        request,
        "clipper/tiktok_cookies_extract.html",
        {
            "token": token,
            "site_base": settings.SITE_BASE_URL.rstrip("/"),
            "invalid": False,
        },
    )


@login_required
@require_GET
def tiktok_cookies_complete(request):
    ok = request.GET.get("ok") == "1"
    error = request.GET.get("error", "")
    return render(
        request,
        "clipper/tiktok_cookies_complete.html",
        {"ok": ok, "error": error},
    )


@api_login_required
def api_posting_preferences(request):
    if request.method == "GET":
        return JsonResponse(get_posting_preferences(request.user))

    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed."}, status=405)

    try:
        body = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON body."}, status=400)

    try:
        prefs = save_posting_preferences(
            request.user,
            post_to_youtube=bool(body.get("post_to_youtube", True)),
            post_to_tiktok=bool(body.get("post_to_tiktok", False)),
            ai_features_enabled=body.get("ai_features_enabled"),
            tiktok_privacy_level=(body.get("tiktok_privacy_level") or "PUBLIC_TO_EVERYONE").strip(),
        )
        return JsonResponse({"success": True, "preferences": prefs})
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)


def _sanitize_panel_workspace(workspace: dict | None) -> dict:
    """Persist last Clip Panel search + selection without oversized payloads."""
    if not isinstance(workspace, dict):
        return {}

    videos = workspace.get("videos") or []
    if not isinstance(videos, list):
        videos = []
    compact_videos = []
    for item in videos[:200]:
        if not isinstance(item, dict):
            continue
        compact_videos.append(
            {
                "id": str(item.get("id") or "")[:100],
                "title": str(item.get("title") or "")[:500],
                "duration": item.get("duration") or 0,
                "source": str(item.get("source") or "tiktok")[:32],
                "url": str(item.get("url") or "")[:500],
                "thumbnail": str(item.get("thumbnail") or "")[:500],
                "twitch_kind": str(item.get("twitch_kind") or "")[:32],
                "user_login": str(item.get("user_login") or "")[:100],
                "user_name": str(item.get("user_name") or "")[:100],
            }
        )

    selected_ids = workspace.get("selectedIds") or workspace.get("selected_ids") or []
    if not isinstance(selected_ids, list):
        selected_ids = []
    selected_ids = [str(item)[:120] for item in selected_ids[:200]]

    return {
        "mode": "grid",
        "videos": compact_videos,
        "selectedIds": selected_ids,
        "currentUsername": str(
            workspace.get("currentUsername") or workspace.get("current_username") or ""
        )[:255],
        "currentSource": str(
            workspace.get("currentSource") or workspace.get("current_source") or "tiktok"
        )[:32],
        "fetchInput": str(workspace.get("fetchInput") or workspace.get("fetch_input") or "")[:500],
        "lastFetchSummary": str(
            workspace.get("lastFetchSummary") or workspace.get("last_fetch_summary") or ""
        )[:500],
        "savedAt": workspace.get("savedAt") or timezone.now().isoformat(),
    }


@api_login_required
def api_panel_workspace(request):
    """Remember last Clip Panel search query, loaded videos, and selection."""
    prefs, _ = PostingPreferences.objects.get_or_create(user=request.user)

    if request.method == "GET":
        return JsonResponse(
            {
                "success": True,
                "workspace": prefs.clip_panel_workspace or {},
            }
        )

    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed."}, status=405)

    try:
        body = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON body."}, status=400)

    workspace = body.get("workspace") if isinstance(body.get("workspace"), dict) else body
    cleaned = _sanitize_panel_workspace(workspace)
    prefs.clip_panel_workspace = cleaned
    prefs.save(update_fields=["clip_panel_workspace", "updated_at"])
    return JsonResponse({"success": True, "workspace": cleaned})



@api_login_required
@require_GET
def api_subscription_status(request):
    return JsonResponse(get_subscription_info(request.user))


@api_login_required
@require_GET
def api_subscription_currencies(request):
    if not settings.NOWPAYMENTS_API_KEY:
        return JsonResponse({"error": "Payments are not configured."}, status=503)
    try:
        currencies = get_available_currencies()
        return JsonResponse(
            {
                "currencies": currencies,
                "price_usd": settings.SUBSCRIPTION_PRICE_USD,
            }
        )
    except NOWPaymentsError as exc:
        return JsonResponse({"error": str(exc)}, status=502)


@api_login_required
@require_POST
def api_subscription_create_payment(request):
    if not settings.NOWPAYMENTS_API_KEY:
        return JsonResponse({"error": "Payments are not configured."}, status=503)

    sub = get_subscription_info(request.user)
    if sub["active"]:
        return JsonResponse({"error": "You already have an active subscription."}, status=400)

    try:
        body = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON body."}, status=400)

    pay_currency = (body.get("pay_currency") or "").strip().lower()
    if not pay_currency:
        return JsonResponse({"error": "Select a cryptocurrency."}, status=400)
    if pay_currency not in ALLOWED_COINS:
        return JsonResponse({"error": "Unsupported cryptocurrency."}, status=400)

    try:
        payment = create_subscription_payment(request.user, pay_currency)
        return JsonResponse(
            {
                "success": True,
                "payment": payment_to_dict(payment),
                "qr_url": f"/api/subscription/payment/{payment.payment_id}/qr/",
            }
        )
    except NOWPaymentsError as exc:
        return JsonResponse({"error": str(exc)}, status=400)


@api_login_required
@require_GET
def api_subscription_payment_status(request, payment_id):
    try:
        payment = SubscriptionPayment.objects.get(
            payment_id=payment_id, user=request.user
        )
    except SubscriptionPayment.DoesNotExist:
        return JsonResponse({"error": "Payment not found."}, status=404)

    try:
        payment = sync_payment_status(payment)
    except NOWPaymentsError as exc:
        return JsonResponse({"error": str(exc)}, status=502)

    return JsonResponse(
        {
            "payment": payment_to_dict(payment),
            "subscription": get_subscription_info(request.user),
        }
    )


@api_login_required
@require_GET
def api_subscription_payment_qr(request, payment_id):
    try:
        payment = SubscriptionPayment.objects.get(
            payment_id=payment_id, user=request.user
        )
    except SubscriptionPayment.DoesNotExist:
        return HttpResponse(status=404)

    png = generate_qr_png(payment)
    return HttpResponse(png, content_type="image/png")


@csrf_exempt
@require_POST
def api_subscription_ipn(request):
    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid payload."}, status=400)

    signature = request.headers.get("x-nowpayments-sig", "")
    if settings.NOWPAYMENTS_IPN_SECRET and not verify_ipn_signature(payload, signature):
        return JsonResponse({"error": "Invalid signature."}, status=403)

    handle_ipn_payload(payload)
    return JsonResponse({"ok": True})


@admin_required
def admin_plans(request):
    return render(
        request,
        "clipper/admin_plans.html",
        {"is_app_admin": True},
    )


@admin_api_required
@require_GET
def api_admin_users(request):
    filter_type = request.GET.get("filter", "all")
    search = request.GET.get("q", "")
    if filter_type not in {"all", "paid", "free"}:
        filter_type = "all"
    return JsonResponse(list_users(filter_type=filter_type, search=search))


@admin_api_required
@require_POST
def api_admin_grant_plan(request):
    try:
        body = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON body."}, status=400)

    User = get_user_model()
    user_id = body.get("user_id")
    username = (body.get("username") or "").strip()
    plan_type = (body.get("plan_type") or "").strip().lower()

    if not plan_type:
        return JsonResponse({"error": "Plan type is required."}, status=400)

    valid_plans = {"none", "unlimited", "daily", "weekly"}
    if plan_type not in valid_plans:
        return JsonResponse({"error": "Invalid plan type."}, status=400)

    target = None
    if user_id:
        target = User.objects.filter(id=user_id).first()
    elif username:
        target = User.objects.filter(username__iexact=username).first()

    if not target:
        return JsonResponse({"error": "User not found."}, status=404)

    if is_admin_user(target) and plan_type == "none":
        return JsonResponse({"error": "Cannot revoke the admin account plan."}, status=400)

    daily_limit = body.get("daily_limit")
    weekly_limit = body.get("weekly_limit")
    duration_days = body.get("duration_days")

    try:
        daily_limit = int(daily_limit) if daily_limit not in (None, "") else None
        weekly_limit = int(weekly_limit) if weekly_limit not in (None, "") else None
        duration_days = int(duration_days) if duration_days not in (None, "") else None
    except (TypeError, ValueError):
        return JsonResponse({"error": "Invalid limit or duration value."}, status=400)

    sub = grant_user_plan(
        user=target,
        plan_type=plan_type,
        granted_by=request.user,
        daily_limit=daily_limit,
        weekly_limit=weekly_limit,
        duration_days=duration_days,
    )

    return JsonResponse(
        {
            "success": True,
            "message": f"Plan updated for {target.username}.",
            "user": list_users(search=target.username)["users"][0],
            "subscription": {
                "plan_type": sub.plan_type,
                "is_active": sub.is_active,
                "expires_at": sub.expires_at.isoformat() if sub.expires_at else None,
            },
        }
    )


@login_required
def upload_schedule(request):
    return render(
        request,
        "clipper/upload_schedule.html",
        {
            "youtube_connected": is_youtube_connected(request.user, request.session),
            "youtube_channel": _youtube_channel(request.user, request.session),
            "tiktok_connected": is_tiktok_connected(request.user),
            "tiktok_account": get_tiktok_account(request.user),
            "subscription": get_subscription_info(request.user),
            "is_app_admin": is_admin_user(request.user),
            "schedule_max": settings.SCHEDULE_MAX_SOURCES,
            "schedule_check_minutes": settings.SCHEDULE_CHECK_MINUTES,
        },
    )


@api_login_required
@require_GET
def api_schedule_sources(request):
    return JsonResponse(list_sources(request.user))


@api_login_required
@require_POST
def api_schedule_add_source(request):
    try:
        body = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON body."}, status=400)

    raw_input = (body.get("input") or "").strip()
    source_type = (body.get("source") or "auto").strip().lower()
    if not raw_input:
        return JsonResponse({"error": "Enter a TikTok or YouTube URL / username."}, status=400)

    if not user_has_any_upload_path(request.user):
        return JsonResponse({"error": "Link YouTube or TikTok before scheduling uploads."}, status=400)

    try:
        source = add_source(request.user, raw_input, source_type=source_type)
        return JsonResponse({"success": True, "source": source_to_dict(source)})
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)


@api_login_required
@require_POST
def api_schedule_update_source(request, source_id):
    try:
        body = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON body."}, status=400)

    raw_input = (body.get("input") or "").strip()
    source_type = (body.get("source") or "auto").strip().lower()
    try:
        source = update_source(
            request.user,
            source_id,
            raw_input=raw_input or None,
            source_type=source_type,
        )
        return JsonResponse({"success": True, "source": source_to_dict(source)})
    except ValueError as exc:
        message = str(exc)
        status = 404 if message == "Source not found." else 400
        return JsonResponse({"error": message}, status=status)


@api_login_required
@require_POST
def api_schedule_remove_source(request, source_id):
    try:
        source = ScheduledSource.objects.get(id=source_id, user=request.user)
    except ScheduledSource.DoesNotExist:
        return JsonResponse({"error": "Source not found."}, status=404)

    source.delete()
    return JsonResponse({"success": True})


@api_login_required
@require_POST
def api_schedule_toggle_source(request, source_id):
    try:
        body = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON body."}, status=400)

    try:
        source = ScheduledSource.objects.get(id=source_id, user=request.user)
    except ScheduledSource.DoesNotExist:
        return JsonResponse({"error": "Source not found."}, status=404)

    source.is_active = bool(body.get("is_active", not source.is_active))
    source.save(update_fields=["is_active", "updated_at"])
    return JsonResponse({"success": True, "source": source_to_dict(source)})


@api_login_required
@require_POST
def api_schedule_run_check(request):
    if not user_has_any_upload_path(request.user):
        return JsonResponse({"error": "Link YouTube or TikTok before scheduling uploads."}, status=400)

    result = check_user_schedules(request.user)
    return JsonResponse({"success": True, **result, "recent": recent_uploads(request.user)})


@api_login_required
@require_GET
def api_schedule_recent(request):
    return JsonResponse({"uploads": recent_uploads(request.user)})
