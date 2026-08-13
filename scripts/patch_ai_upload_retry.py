#!/usr/bin/env python3
"""Patch ai_upload_retry.py: fix NameError in analyze_failure_with_ai."""
from __future__ import annotations

import ast
import sys
from pathlib import Path

path = Path(sys.argv[1] if len(sys.argv) > 1 else "/root/tiktok-cliper/clipper/services/ai_upload_retry.py")
text = path.read_text(encoding="utf-8")

old_sig = """def analyze_failure_with_ai(
    error: Exception,
    *,
    state: VideoAttemptState,
    stage: str,
) -> dict[str, Any]:"""

new_sig = """def analyze_failure_with_ai(
    error: Exception,
    *,
    state: VideoAttemptState,
    stage: str,
    user=None,
) -> dict[str, Any]:"""

if old_sig not in text:
    if "user=None," in text and "def analyze_failure_with_ai(" in text:
        print("already patched signature")
    else:
        raise SystemExit("signature not found")
else:
    text = text.replace(old_sig, new_sig, 1)

replacements = [
    (
        'recovery = analyze_failure_with_ai(exc, state=state, stage="fix")',
        'recovery = analyze_failure_with_ai(exc, state=state, stage="fix", user=user)',
    ),
    (
        "recovery = analyze_failure_with_ai(exc, state=state, stage=stage)",
        "recovery = analyze_failure_with_ai(exc, state=state, stage=stage, user=user)",
    ),
]
for old, new in replacements:
    if new in text:
        continue
    if old not in text:
        raise SystemExit(f"call site not found: {old}")
    text = text.replace(old, new)

path.write_text(text, encoding="utf-8")
ast.parse(path.read_text(encoding="utf-8"))
print(f"patched {path}")
for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
    if "analyze_failure_with_ai" in line or "_heuristic_recovery(error, stage, user=user)" in line:
        print(f"{i}: {line.rstrip()}")
    if 125 <= i <= 136:
        print(f"{i}: {line.rstrip()}")
