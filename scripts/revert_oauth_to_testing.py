#!/usr/bin/env python3
"""Revert Google Cloud OAuth consent screen from Production to Testing for all pool projects.

Requires an authenticated Chrome session with CDP on port 9222, OR run headed after manual login.

Usage:
  1. Sign in to https://console.cloud.google.com in Chrome
  2. python3 scripts/revert_oauth_to_testing.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

PROJECTS = [
    "wishfox-clip-pool-c",
    "wishfox-yt-extra-1-cd35",
    "wishfox-clip-pool-h",
    "key-journal-505020-k7",
    "wishfox-yt-pool-5",
    "youtubelink-500704",
    "copper-site-500801-i2",
    "my-project-3-500802",
    "my-project-4-500802",
]

TEST_USERS = [
    "badmandog66@gmail.com",
    "davidmargret458@gmail.com",
    "skyblaick@gmail.com",
]

CONSENT_URL = "https://console.cloud.google.com/apis/credentials/consent?project={project_id}"


def _page_text(page) -> str:
    try:
        return page.inner_text("body")
    except Exception:
        return ""


def _is_sign_in(page) -> bool:
    url = page.url
    text = _page_text(page).lower()
    return "accounts.google.com" in url or "sign in" in text[:500] and "cloud console" not in text


def _detect_status(page) -> str:
    text = _page_text(page)
    lower = text.lower()
    if "in production" in lower or "publishing status" in lower and "production" in lower:
        return "production"
    if "testing" in lower and ("publishing status" in lower or "user type" in lower):
        return "testing"
    if "back to testing" in lower or "revert to testing" in lower:
        return "production"
    return "unknown"


def _click_back_to_testing(page) -> bool:
    selectors = [
        "text=Back to testing",
        "text=Revert to testing",
        "text=BACK TO TESTING",
        "button:has-text('Back to testing')",
        "button:has-text('Revert to testing')",
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible():
                loc.click(timeout=5000)
                time.sleep(1)
                # Confirm dialog
                for confirm in ["text=Confirm", "text=Revert", "button:has-text('Confirm')"]:
                    try:
                        c = page.locator(confirm).first
                        if c.count() and c.is_visible():
                            c.click(timeout=3000)
                            time.sleep(2)
                            break
                    except Exception:
                        pass
                return True
        except Exception:
            continue
    return False


def revert_project(page, project_id: str) -> dict:
    url = CONSENT_URL.format(project_id=project_id)
    page.goto(url, wait_until="domcontentloaded", timeout=90000)
    time.sleep(3)

    if _is_sign_in(page):
        return {
            "project_id": project_id,
            "before": "unknown",
            "after": "unknown",
            "ok": False,
            "note": "Not signed in to Google Cloud Console",
        }

    before = _detect_status(page)
    if before == "testing":
        return {
            "project_id": project_id,
            "before": "testing",
            "after": "testing",
            "ok": True,
            "note": "Already in Testing",
        }

    clicked = _click_back_to_testing(page)
    time.sleep(3)
    after = _detect_status(page)

    return {
        "project_id": project_id,
        "before": before,
        "after": after,
        "ok": after == "testing" or (clicked and after != "production"),
        "note": "Reverted via Back to testing" if clicked else "Could not find Back to testing button",
    }


def main() -> int:
    cdp = "http://127.0.0.1:9222"
    results = []

    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(cdp)
        except Exception as exc:
            print(f"ERROR: Cannot connect to Chrome CDP at {cdp}: {exc}")
            print("Start Chrome with --remote-debugging-port=9222 and sign in to console.cloud.google.com")
            return 1

        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.new_page()

        for pid in PROJECTS:
            print(f"Processing {pid}...")
            result = revert_project(page, pid)
            results.append(result)
            status = "OK" if result["ok"] else "FAIL"
            print(f"  [{status}] {result['before']} -> {result['after']}: {result['note']}")

        page.close()

    print("\n=== SUMMARY ===")
    print(f"{'Project':<30} {'Before':<12} {'After':<12} {'OK'}")
    print("-" * 70)
    for r in results:
        print(f"{r['project_id']:<30} {r['before']:<12} {r['after']:<12} {r['ok']}")

    failed = [r for r in results if not r["ok"]]
    if failed:
        print(f"\n{len(failed)} project(s) need manual fix or Google sign-in.")
        return 1
    print("\nAll 9 projects are in Testing mode.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
