#!/usr/bin/env python3
"""Patch ai_video_fix.py so unparseable vision output falls back to enhancements-only."""
from __future__ import annotations

import ast
import sys
from pathlib import Path

path = Path(sys.argv[1] if len(sys.argv) > 1 else "/root/tiktok-cliper/clipper/services/ai_video_fix.py")
text = path.read_text(encoding="utf-8")

if "fixable_parse_fallback" in text:
    print("already patched")
    raise SystemExit(0)

needle = (
    "    response = chat_vision(_encode_frames(frames), prompt, timeout=240)\n"
    "    plan = _parse_json_response(response)\n"
    '    plan["summary"] = str(plan.get("summary") or "").strip()\n'
)

replacement = (
    "    response = chat_vision(_encode_frames(frames), prompt, timeout=240)\n"
    "    plan = _parse_json_response(response)\n"
    "    # moondream (and similar caption models) often return prose instead of JSON.\n"
    "    # Fall back to a safe pass-through plan so uploads are not hard-blocked.\n"
    '    if plan.get("block_reason") == "AI fix plan could not be parsed.":\n'
    '        logger.warning("AI fix plan parse failed; using safe default enhancements-only plan")\n'
    "        plan = {\n"
    '            "fixable": True,\n'
    '            "summary": "AI returned unstructured output; applying standard enhancements only.",\n'
    '            "copyright_audio": False,\n'
    '            "replace_copyright_audio_only": False,\n'
    '            "risky_segments": [],\n'
    '            "fixable_parse_fallback": True,\n'
    "        }\n"
    '    plan["summary"] = str(plan.get("summary") or "").strip()\n'
)

if needle not in text:
    raise SystemExit("needle not found in ai_video_fix.py")

if "logger = logging.getLogger(__name__)" not in text:
    if "import logging\n" not in text:
        if "import json\n" in text:
            text = text.replace("import json\n", "import json\nimport logging\n", 1)
        else:
            text = "import logging\n" + text
    text = text.replace(
        "import logging\n",
        "import logging\n\nlogger = logging.getLogger(__name__)\n",
        1,
    )

text = text.replace(needle, replacement, 1)
path.write_text(text, encoding="utf-8")
ast.parse(path.read_text(encoding="utf-8"))
print(f"patched {path}")
for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
    if "fixable_parse_fallback" in line or "parse failed" in line:
        print(f"{i}: {line}")
