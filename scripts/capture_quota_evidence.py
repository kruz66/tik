#!/usr/bin/env python3
"""Capture screenshots for YouTube quota extension form evidence."""

from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path("/opt/cursor/artifacts/quota-form")
OUT.mkdir(parents=True, exist_ok=True)
BASE = "https://mekesh.work.gd:8001"


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1400, "height": 900})

        for name, url in [
            ("01-homepage", f"{BASE}/"),
            ("02-privacy-policy", f"{BASE}/privacy/"),
            ("03-terms-of-service", f"{BASE}/terms/"),
        ]:
            page.goto(url, wait_until="networkidle", timeout=60000)
            page.screenshot(path=str(OUT / f"{name}.png"), full_page=True)
            print("saved", name)

        # Dashboard (may redirect to login)
        page.goto(f"{BASE}/dashboard/", wait_until="networkidle", timeout=60000)
        if "login" in page.url.lower() or page.locator('input[name="username"], input#id_username').count():
            user = page.locator('input[name="username"], input#id_username').first
            pwd = page.locator('input[name="password"], input#id_password').first
            if user.count() and pwd.count():
                user.fill("wishfox")
                pwd.fill("wishfox2626")
                page.locator('button[type="submit"], input[type="submit"]').first.click()
                page.wait_for_load_state("networkidle", timeout=60000)

        page.goto(f"{BASE}/dashboard/", wait_until="networkidle", timeout=60000)
        page.screenshot(path=str(OUT / "04-dashboard.png"), full_page=True)
        print("saved 04-dashboard")

        browser.close()

    print(f"\nScreenshots in {OUT}")


if __name__ == "__main__":
    main()
