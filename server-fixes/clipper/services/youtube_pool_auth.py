from django.shortcuts import redirect
from urllib.parse import quote

from clipper.services.youtube import build_redirect_uri, get_authorization_url, pool_configured
from clipper.services.youtube_project_pool import load_projects
from clipper.services.zernio_youtube import get_connection as get_yt_connection


def projects_needing_oauth(user) -> list[str]:
    """Pool projects that do not yet have stored OAuth credentials for this user."""
    if not user or not getattr(user, "is_authenticated", False):
        return [p["project_id"] for p in load_projects()]
    connection = get_yt_connection(user)
    creds_map = (connection.project_credentials or {}) if connection else {}
    if not isinstance(creds_map, dict):
        creds_map = {}
    missing = []
    for project in load_projects():
        pid = project["project_id"]
        if not creds_map.get(pid):
            missing.append(pid)
    return missing


def _redirect_oauth_for_project(request, project_id: str):
    redirect_uri = build_redirect_uri(request)
    # Pool linking must use identical scopes on every project or Google returns
    # "Scope has changed" and breaks the OAuth chain.
    include_analytics = False
    request.session["youtube_oauth_include_analytics"] = False
    auth_url, state, code_verifier = get_authorization_url(
        project_id,
        redirect_uri,
        include_analytics=include_analytics,
    )
    request.session["youtube_oauth_project_id"] = project_id
    request.session["youtube_oauth_state"] = state
    request.session["youtube_oauth_code_verifier"] = code_verifier
    request.session["youtube_redirect_uri"] = redirect_uri
    request.session.modified = True
    return redirect(auth_url)


def start_pool_oauth(request):
    """Start Google OAuth; chains through all pool projects missing credentials."""
    if not pool_configured():
        return redirect(
            "/dashboard/?youtube_error="
            + quote("YouTube OAuth project pool is not configured.")
        )

    queue = request.session.get("youtube_oauth_pool_queue")
    if queue is None:
        queue = projects_needing_oauth(request.user)

    if not queue:
        linked = len(projects_needing_oauth(request.user))
        total = len(load_projects())
        done = total - linked
        request.session["youtube_oauth_pool_queue"] = []
        request.session.modified = True
        return redirect(
            f"/dashboard/?youtube_connected=1&pool_linked={done}&pool_total={total}"
        )

    project_id = queue[0]
    request.session["youtube_oauth_pool_queue"] = queue[1:]
    request.session.modified = True
    return _redirect_oauth_for_project(request, project_id)


def continue_pool_oauth_queue(request):
    """After a successful callback, OAuth the next pool project if any remain."""
    queue = request.session.get("youtube_oauth_pool_queue") or []
    if not queue:
        return None
    project_id = queue[0]
    request.session["youtube_oauth_pool_queue"] = queue[1:]
    request.session.modified = True
    return _redirect_oauth_for_project(request, project_id)
