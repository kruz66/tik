#!/usr/bin/env python3
"""Generate YouTube Data API quota increase request pack for all pool GCP projects."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "youtube_quota_config.json"


def main() -> int:
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    projects = cfg["projects"]
    units = cfg["units_per_project_requested"]
    total = cfg["target_daily_uploads_total"]

    print("# YouTube Data API Quota Increase Pack\n")
    print(f"**App:** {cfg['app_name']}")
    print(f"**Target:** {total} uploads/day (~{cfg['total_units_per_day']:,} units/day total)")
    print(f"**Request per project:** {units:,} units/day\n")
    print(f"**Official form:** {cfg['quota_form_url']}\n")

    print("## Important")
    print("- Submit one Audit and Quota Extension form per GCP project (or explain pool in one audit).")
    print("- Google requires OAuth verification + compliance audit before large increases.")
    print("- Quota resets midnight Pacific Time.\n")

    print("## Form answers (copy/paste)\n")
    print("**Reason:** Quota extension request (requires compliance audit)\n")
    print("**Organization website:**", cfg["app_url"])
    print("**Category:**", cfg["category"])
    print("\n**How you use YouTube API Services:**")
    print(cfg["use_case_summary"])
    print("\n**Why you need additional quota:**")
    print(cfg["quota_justification"])
    print("\n**Expected daily API volume:**")
    print(f"- videos.insert: up to {total} calls/day (~{total * cfg['units_per_upload']:,} units)")
    print("- channels.list, thumbnails.set: low volume relative to uploads\n")

    print("## Per-project console links\n")
    for p in projects:
        pid = p["project_id"]
        url = cfg["quota_console_pattern"].format(project_id=pid)
        print(f"- [{pid}]({url})")

    print("\n## Per-project form checklist\n")
    for i, p in enumerate(projects, 1):
        pid = p["project_id"]
        print(f"### {i}. {pid}")
        print(f"- Open quotas: {cfg['quota_console_pattern'].format(project_id=pid)}")
        print(f"- Request **Queries per day** increase to **{units:,}**")
        print(f"- OAuth client ID: see `{p['client_secrets_file']}` on server")
        print(f"- Privacy: {cfg['privacy_url']}")
        print(f"- Terms: {cfg['terms_url']}")
        print(f"- Demo: {cfg['demo_video_url']}\n")

    out = ROOT.parent / "oauth_output" / "quota_increase_pack.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    # Re-run through stdout capture for file
    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        # duplicate print logic simplified - write markdown file
        pass

    lines = [
        f"YouTube Quota Increase Pack - {cfg['app_name']}",
        f"Target: {total} uploads/day, {units:,} units/project/day",
        f"Form: {cfg['quota_form_url']}",
        "",
        cfg["use_case_summary"],
        "",
        cfg["quota_justification"],
        "",
        "Projects:",
    ]
    for p in projects:
        lines.append(f"  - {p['project_id']}")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nWrote summary: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
