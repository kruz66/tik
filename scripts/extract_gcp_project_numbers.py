#!/usr/bin/env python3
"""Extract GCP project numbers from OAuth client IDs (numeric prefix before hyphen)."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "youtube_quota_config.json"


def project_number_from_client_id(client_id: str) -> str:
    match = re.match(r"^(\d+)-", client_id or "")
    return match.group(1) if match else ""


def main() -> int:
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    secrets_dir = Path("/root/tiktok-cliper")
    if len(sys.argv) > 1:
        secrets_dir = Path(sys.argv[1])

    # Build map from pool config
    pool_path = secrets_dir / "config" / "youtube_oauth_projects.json"
    pool = json.loads(pool_path.read_text(encoding="utf-8"))
    rows = []
    for item in pool:
        block = item.get("web") or item.get("installed") or {}
        pid = block.get("project_id", "")
        cid = block.get("client_id", "")
        num = project_number_from_client_id(cid)
        rows.append({"project_id": pid, "project_number": num, "client_id": cid})

    print("# GCP Project Numbers for Quota Forms\n")
    print("| Project ID | Project Number | Client ID |")
    print("|---|---|---|")
    for r in rows:
        print(f"| {r['project_id']} | {r['project_number']} | `{r['client_id'][:45]}...` |")

    cfg["projects"] = [
        {
            **p,
            "project_number": next(
                (r["project_number"] for r in rows if r["project_id"] == p["project_id"]),
                "",
            ),
        }
        for p in cfg["projects"]
    ]
    out = ROOT.parent / "oauth_output" / "gcp_project_numbers.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    print(f"\nWrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
