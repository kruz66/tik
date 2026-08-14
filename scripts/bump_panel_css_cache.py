#!/usr/bin/env python3
from pathlib import Path
import re

p = Path("/root/tiktok-cliper/templates/clipper/panel.html")
t = p.read_text(encoding="utf-8")
t2, n = re.subn(r"(panel\.css' %\}\?v=)([^\s\"']+)", r"\g<1>20260814a", t, count=1)
if not n:
    t2, n = re.subn(r"(panel\.css.*?v=)([^\s\"']+)", r"\g<1>20260814a", t, count=1)
if not n:
    for i, line in enumerate(t.splitlines(), 1):
        if "panel.css" in line:
            print(i, line)
    raise SystemExit("no bump")
p.write_text(t2, encoding="utf-8")
print("bumped", n)
for i, line in enumerate(t2.splitlines(), 1):
    if "panel.css" in line:
        print(i, line)
