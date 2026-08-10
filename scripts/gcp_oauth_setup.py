#!/usr/bin/env python3
"""
Automate Google Cloud OAuth pool setup for WishFox / Youtube Link.

Run on YOUR local PC while signed into Google Cloud Console in Chrome or Brave.

Recommended (keeps your existing login — no password in script):
  1. Close the browser completely.
  2. Start with remote debugging (PowerShell examples):

     Brave:
       & "C:/Program Files/BraveSoftware/Brave-Browser/Application/brave.exe" `
         --remote-debugging-port=9222 `
         --user-data-dir="$env:LOCALAPPDATA/BraveSoftware/Brave-Browser/User Data"

     Chrome:
       & "C:/Program Files/Google/Chrome/Application/chrome.exe" `
         --remote-debugging-port=9222 `
         --user-data-dir="$env:LOCALAPPDATA/Google/Chrome/User Data"

  3. Sign in to https://console.cloud.google.com if needed.
  4. pip install -r scripts/requirements-oauth-setup.txt
  5. playwright install chromium   # only needed if not using CDP
  6. copy scripts/oauth_setup_config.example.json oauth_setup_config.json
  7. python scripts/gcp_oauth_setup.py --config oauth_setup_config.json

Outputs:
  oauth_output/client_secrets1.json … client_secrets9.json
  oauth_output/setup_results.json
  oauth_output/screenshots/   (on errors)

Options:
  --config PATH          JSON config (default: oauth_setup_config.json)
  --cdp URL              Override cdp_url (e.g. http://127.0.0.1:9222)
  --projects ID1,ID2     Skip project creation; configure these IDs only
  --dry-run              Print plan without browser actions
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    sync_playwright,
)

ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = ROOT.parent / "oauth_setup_config.json"
EXAMPLE_CONFIG = ROOT / "oauth_setup_config.example.json"

REDIRECT_DEFAULT = "https://mekesh.work.gd:8001/api/youtube/callback/"
YOUTUBE_API = "youtube.googleapis.com"


@dataclass
class SetupConfig:
    email: str = ""
    test_users: list[str] = field(default_factory=list)
    num_projects: int = 9
    project_name_prefix: str = "WishFox Clip Pool"
    app_name: str = "WishFox Clipper"
    redirect_uri: str = REDIRECT_DEFAULT
    output_dir: str = "./oauth_output"
    cdp_url: str = "http://127.0.0.1:9222"
    browser_executable: str = ""
    user_data_dir: str = ""
    headless: bool = False
    slow_mo_ms: int = 100
    page_load_wait_ms: int = 3000
    existing_project_ids: list[str] = field(default_factory=list)
    youtube_scopes: list[str] = field(
        default_factory=lambda: [
            "https://www.googleapis.com/auth/youtube.upload",
            "https://www.googleapis.com/auth/youtube.readonly",
            "https://www.googleapis.com/auth/youtube",
        ]
    )

    @classmethod
    def load(cls, path: Path) -> "SetupConfig":
        data = json.loads(path.read_text(encoding="utf-8"))
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def output_path(self) -> Path:
        p = Path(self.output_dir)
        if not p.is_absolute():
            p = ROOT.parent / p
        p.mkdir(parents=True, exist_ok=True)
        (p / "screenshots").mkdir(exist_ok=True)
        return p


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def wait(page: Page, ms: int) -> None:
    page.wait_for_timeout(ms)


def screenshot(page: Page, cfg: SetupConfig, name: str) -> None:
    path = cfg.output_path() / "screenshots" / f"{name}.png"
    try:
        page.screenshot(path=str(path), full_page=True)
        log(f"screenshot: {path}")
    except Exception as exc:
        log(f"screenshot failed: {exc}")


def click_first(page: Page, patterns: list[str], timeout: float = 8000) -> bool:
    for pat in patterns:
        loc = page.get_by_role("button", name=re.compile(pat, re.I))
        if loc.count():
            try:
                loc.first.click(timeout=timeout)
                return True
            except Exception:
                pass
        loc = page.get_by_text(re.compile(pat, re.I))
        if loc.count():
            try:
                loc.first.click(timeout=timeout)
                return True
            except Exception:
                pass
    return False


def fill_first(page: Page, selectors: str, value: str) -> bool:
    for sel in selectors.split(","):
        sel = sel.strip()
        loc = page.locator(sel)
        if loc.count():
            try:
                el = loc.first
                if el.is_visible():
                    el.click()
                    el.fill(value)
                    return True
            except Exception:
                pass
    return False


def body_text(page: Page) -> str:
    try:
        return page.inner_text("body")
    except Exception:
        return ""


def goto(page: Page, url: str, cfg: SetupConfig) -> None:
    log(f"→ {url}")
    page.goto(url, wait_until="domcontentloaded", timeout=120_000)
    wait(page, cfg.page_load_wait_ms)


def detect_email(page: Page, cfg: SetupConfig) -> str:
    if cfg.email:
        return cfg.email
    goto(page, "https://console.cloud.google.com/home/dashboard", cfg)
    # Account chip / profile area often shows email
    text = body_text(page)
    m = re.search(r"[\w.+-]+@gmail\.com", text)
    if m:
        log(f"Detected email: {m.group(0)}")
        return m.group(0)
    raise RuntimeError(
        "Could not detect signed-in email. Set \"email\" in your config JSON."
    )


def ensure_signed_in(page: Page, cfg: SetupConfig) -> None:
    goto(page, "https://console.cloud.google.com/cloud-resource-manager", cfg)
    if "accounts.google.com" in page.url:
        raise RuntimeError(
            "Not signed in to Google Cloud. Sign in in the browser window, then re-run."
        )
    log("Signed in to Google Cloud Console.")


def create_gcp_project(page: Page, cfg: SetupConfig, display_name: str) -> str:
    goto(page, "https://console.cloud.google.com/projectcreate", cfg)
    wait(page, 2000)

    if not fill_first(
        page,
        'input[aria-label*="Project name" i],input[formcontrolname="projectName"],input[name="name"]',
        display_name,
    ):
        screenshot(page, cfg, "create_project_no_name_field")
        raise RuntimeError(f"Could not find project name field for {display_name}")

    wait(page, 1000)

    # Project ID is often auto-filled; try to read it
    project_id = ""
    id_loc = page.locator(
        'input[aria-label*="Project ID" i],input[formcontrolname="projectId"]'
    )
    if id_loc.count():
        try:
            project_id = id_loc.first.input_value().strip()
        except Exception:
            project_id = ""

    if not project_id:
        project_id = re.sub(r"[^a-z0-9-]", "-", display_name.lower())
        project_id = re.sub(r"-+", "-", project_id).strip("-")[:30]

    if not click_first(page, [r"^Create$", "Create project"]):
        screenshot(page, cfg, "create_project_no_create_btn")
        raise RuntimeError(f"Could not click Create for {display_name}")

    wait(page, 8000)

    # Confirm via resource manager
    goto(page, "https://console.cloud.google.com/cloud-resource-manager", cfg)
    wait(page, 2000)
    row = page.get_by_text(re.compile(re.escape(display_name), re.I))
    if row.count():
        row.first.click()
        wait(page, 1500)

    m = re.search(r"project=([a-z][a-z0-9-]{4,62})", page.url)
    if m:
        project_id = m.group(1)

    log(f"Created project: {display_name} → {project_id}")
    return project_id


def enable_youtube_api(page: Page, cfg: SetupConfig, project_id: str) -> None:
    goto(
        page,
        f"https://console.cloud.google.com/apis/library/{YOUTUBE_API}?project={project_id}",
        cfg,
    )
    text = body_text(page)
    if "API enabled" in text or "Manage" in text:
        log(f"YouTube Data API v3 already enabled for {project_id}")
        return
    if click_first(page, ["^Enable$", "Enable API"]):
        wait(page, 6000)
        log(f"Enabled YouTube Data API v3 for {project_id}")
    else:
        log(f"Enable button not found for {project_id} (may already be on)")


def configure_auth_platform(page: Page, cfg: SetupConfig, project_id: str, email: str) -> None:
    """OAuth consent / Auth Platform: External, branding, scopes, test users."""
    goto(page, f"https://console.cloud.google.com/auth/overview?project={project_id}", cfg)

    if click_first(page, ["Get started", "Configure consent", "Create OAuth client"]):
        wait(page, 2000)

    # --- Audience (External + Testing + test users) ---
    goto(page, f"https://console.cloud.google.com/auth/audience?project={project_id}", cfg)
    text = body_text(page)

    if "External" in text:
        click_first(page, ["External"])
        wait(page, 1000)
        click_first(page, ["^Next$", "Continue", "Create"])
        wait(page, 2000)

    if "Testing" in text or "Publish app" in text:
        click_first(page, ["^Testing$"])
        wait(page, 1000)

    if click_first(page, ["Add users", "Add test users"]):
        wait(page, 1500)
        box = page.locator('textarea, input[aria-label*="email" i]').first
        if box.count():
            box.fill("\n".join(cfg.test_users))
            click_first(page, ["^Add$", "^Save$", "Done"])
            wait(page, 2000)
            log(f"Added test users for {project_id}")

    # --- Branding / App info ---
    goto(page, f"https://console.cloud.google.com/auth/branding?project={project_id}", cfg)
    fill_first(
        page,
        'input[aria-label*="App name" i],input[formcontrolname="displayName"]',
        cfg.app_name,
    )
    fill_first(
        page,
        'input[type="email"],input[aria-label*="User support email" i],input[aria-label*="email" i]',
        email,
    )
    fill_first(
        page,
        'input[aria-label*="Developer contact" i],input[aria-label*="Contact" i]',
        email,
    )
    click_first(page, ["^Save$", "Save and continue"])
    wait(page, 2500)

    # --- Data Access / Scopes ---
    goto(page, f"https://console.cloud.google.com/auth/scopes?project={project_id}", cfg)
    if click_first(page, ["Add or remove scopes", "Add scopes", "Edit"]):
        wait(page, 2000)
        for scope in cfg.youtube_scopes:
            short = scope.rsplit("/", 1)[-1]
            search = page.locator(
                'input[type="search"],input[placeholder*="Filter" i],input[aria-label*="Filter" i]'
            )
            if search.count():
                search.first.fill(short)
                wait(page, 800)
            row = page.get_by_text(re.compile(re.escape(short), re.I))
            if row.count():
                row.first.click()
                wait(page, 400)
        click_first(page, ["^Update$", "^Save$", "Done"])
        wait(page, 2000)
        log(f"Configured scopes for {project_id}")

    click_first(page, ["^Save$"])
    wait(page, 1500)


def create_oauth_client_and_download(
    page: Page, cfg: SetupConfig, project_id: str, secret_index: int
) -> Path | None:
    """Create Web OAuth client, download JSON as client_secrets{N}.json."""
    out = cfg.output_path() / f"client_secrets{secret_index}.json"
    client_name = f"{cfg.app_name} Web {secret_index}"

    goto(page, f"https://console.cloud.google.com/auth/clients?project={project_id}", cfg)

    # If client with redirect already exists, try download first
    if cfg.redirect_uri in body_text(page):
        path = _try_download_existing_client(page, cfg, out, project_id)
        if path:
            return path

    goto(page, f"https://console.cloud.google.com/auth/clients/create?project={project_id}", cfg)
    wait(page, 2000)

    click_first(page, ["Web application", "Web client", "Web"])
    wait(page, 1000)

    fill_first(
        page,
        'input[aria-label*="Name" i],input[formcontrolname="displayName"]',
        client_name,
    )

    # Redirect URI — may need "Add URI" first
    if click_first(page, ["Add URI", "Add redirect URI"]):
        wait(page, 800)
    fill_first(
        page,
        'input[aria-label*="redirect" i],input[placeholder*="https://" i]',
        cfg.redirect_uri,
    )

    if not click_first(page, ["^Create$", "Create OAuth client"]):
        screenshot(page, cfg, f"create_client_fail_{project_id}")
        raise RuntimeError(f"Could not create OAuth client for {project_id}")

    wait(page, 4000)

    # Post-create dialog sometimes shows client id/secret + download
    path = _try_download_existing_client(page, cfg, out, project_id)
    if path:
        return path

    # Fallback: copy from dialog text if download button missing (new GCP UI)
    path = _extract_client_from_page_text(page, cfg, out, project_id)
    if path:
        return path

    screenshot(page, cfg, f"download_fail_{project_id}")
    log(f"WARNING: Could not auto-download JSON for {project_id}. Configure manually.")
    return None


def _try_download_existing_client(
    page: Page, cfg: SetupConfig, dest: Path, project_id: str
) -> Path | None:
    goto(page, f"https://console.cloud.google.com/auth/clients?project={project_id}", cfg)

    for label in ["Download JSON", "Download", "JSON"]:
        btn = page.get_by_role("button", name=re.compile(label, re.I))
        if not btn.count():
            btn = page.get_by_text(re.compile(label, re.I))
        if btn.count():
            try:
                with page.expect_download(timeout=30_000) as dl_info:
                    btn.first.click()
                download = dl_info.value
                download.save_as(str(dest))
                log(f"Downloaded {dest.name} for {project_id}")
                return dest
            except Exception:
                pass

    # Row menu → Download
    menu = page.locator('[aria-label*="Download" i],[aria-label*="More" i]')
    if menu.count():
        try:
            with page.expect_download(timeout=30_000) as dl_info:
                menu.first.click()
                click_first(page, ["Download JSON", "Download"])
            download = dl_info.value
            download.save_as(str(dest))
            log(f"Downloaded {dest.name} for {project_id}")
            return dest
        except Exception:
            pass
    return None


def _extract_client_from_page_text(
    page: Page, cfg: SetupConfig, dest: Path, project_id: str
) -> Path | None:
    text = body_text(page)
    cid = re.search(r"(\d+-[a-z0-9]+\.apps\.googleusercontent\.com)", text)
    secret = re.search(r"(GOCSPX-[A-Za-z0-9_-]+)", text)
    if not cid or not secret:
        return None
    payload = {
        "web": {
            "client_id": cid.group(1),
            "project_id": project_id,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "client_secret": secret.group(1),
            "redirect_uris": [cfg.redirect_uri],
        }
    }
    dest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    log(f"Built {dest.name} from on-screen credentials for {project_id}")
    return dest


def connect_browser(p: Playwright, cfg: SetupConfig) -> tuple[Browser | None, BrowserContext, Page, bool]:
    """Returns (browser, context, page, attached_via_cdp)."""
    if cfg.cdp_url:
        log(f"Connecting to browser via CDP: {cfg.cdp_url}")
        browser = p.chromium.connect_over_cdp(cfg.cdp_url)
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.pages[0] if context.pages else context.new_page()
        return browser, context, page, True

    launch_kwargs: dict[str, Any] = {
        "headless": cfg.headless,
        "slow_mo": cfg.slow_mo_ms,
        "viewport": {"width": 1400, "height": 900},
        "accept_downloads": True,
        "ignore_default_args": ["--enable-automation"],
        "args": ["--disable-blink-features=AutomationControlled"],
    }
    if cfg.browser_executable:
        launch_kwargs["executable_path"] = cfg.browser_executable

    profile = cfg.user_data_dir or str(ROOT.parent / ".playwright-gcp-profile")
    Path(profile).mkdir(parents=True, exist_ok=True)
    log(f"Launching persistent browser profile: {profile}")
    context = p.chromium.launch_persistent_context(profile, **launch_kwargs)
    page = context.pages[0] if context.pages else context.new_page()
    return None, context, page, False


def run_setup(cfg: SetupConfig, project_ids_override: list[str] | None = None) -> dict:
    results: list[dict] = []
    out = cfg.output_path()

    with sync_playwright() as p:
        browser, context, page, attached = connect_browser(p, cfg)
        try:
            ensure_signed_in(page, cfg)
            email = detect_email(page, cfg)

            if project_ids_override:
                project_ids = project_ids_override
            elif cfg.existing_project_ids:
                project_ids = list(cfg.existing_project_ids)
            else:
                project_ids = []
                for i in range(1, cfg.num_projects + 1):
                    name = f"{cfg.project_name_prefix} {i}"
                    pid = create_gcp_project(page, cfg, name)
                    project_ids.append(pid)

            for idx, project_id in enumerate(project_ids, start=1):
                log(f"=== [{idx}/{len(project_ids)}] {project_id} ===")
                entry: dict[str, Any] = {"index": idx, "project_id": project_id}
                try:
                    enable_youtube_api(page, cfg, project_id)
                    configure_auth_platform(page, cfg, project_id, email)
                    secret_path = create_oauth_client_and_download(page, cfg, project_id, idx)
                    entry["client_secrets"] = str(secret_path) if secret_path else None
                    entry["status"] = "ok" if secret_path else "partial"
                except Exception as exc:
                    entry["status"] = "error"
                    entry["error"] = str(exc)
                    screenshot(page, cfg, f"error_{project_id}")
                    log(f"ERROR on {project_id}: {exc}")
                results.append(entry)

        finally:
            if not attached:
                context.close()
            elif browser:
                browser.close()

    summary = {
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "email": cfg.email,
        "redirect_uri": cfg.redirect_uri,
        "projects": results,
    }
    summary_path = out / "setup_results.json"
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    log(f"Wrote {summary_path}")
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="JSON config path")
    parser.add_argument("--cdp", dest="cdp_url", default="", help="CDP URL override")
    parser.add_argument(
        "--projects",
        default="",
        help="Comma-separated existing project IDs (skip creation)",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config_path = args.config
    if not config_path.exists():
        if EXAMPLE_CONFIG.exists():
            log(f"Config not found at {config_path}")
            log(f"Copy example: copy {EXAMPLE_CONFIG} {config_path}")
        else:
            log(f"Config not found: {config_path}")
        return 1

    cfg = SetupConfig.load(config_path)
    if args.cdp_url:
        cfg.cdp_url = args.cdp_url

    project_override = [p.strip() for p in args.projects.split(",") if p.strip()] or None

    if args.dry_run:
        log("DRY RUN")
        log(f"  email: {cfg.email or '(auto-detect)'}")
        log(f"  projects: {project_override or cfg.existing_project_ids or cfg.num_projects}")
        log(f"  output: {cfg.output_path()}")
        log(f"  cdp: {cfg.cdp_url}")
        return 0

    summary = run_setup(cfg, project_override)
    ok = sum(1 for r in summary["projects"] if r.get("status") == "ok")
    total = len(summary["projects"])
    log(f"Done: {ok}/{total} projects with downloaded secrets.")
    return 0 if ok == total else 1


if __name__ == "__main__":
    sys.exit(main())
