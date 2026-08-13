# YouTube upload diagnosis (updated 2026-08-13)

## Infrastructure remembered
- App server: `root@98.142.250.176` (`mekesh.work.gd:8001`) → `/root/tiktok-cliper`
- AI / Ollama host: `172.86.119.144` via SSH (`OLLAMA_SSH_HOST`), user `wishfox`
- Vision model currently: `moondream` (caption-only; poor at JSON plans)
- Text model: `llama3.2:3b`

## OAuth / pool (wishfox)
- connected: true — Code Matrix
- pool_linked: 9/9

## Fixes applied on live server
1. **`ai_upload_retry.py`** — `analyze_failure_with_ai(..., user=None)`  
   Fixes `[YouTube] name 'user' is not defined` when Ollama recovery fails.
2. **`ai_video_fix.py`** — if moondream returns prose instead of JSON, use safe enhancements-only fallback (`fixable_parse_fallback`).

## Remaining blocker for successful upload
YouTube **channel daily upload limit** for Testing/unverified OAuth apps:

`YouTube channel daily upload limit reached for this Google account. Unverified OAuth apps can only upload a few videos per day until Google approves verification.`

Retest after ~midnight Pacific, or use another channel, or complete OAuth verification.

## Evidence jobs
- NameError (before fix): job `43ec9089-...`
- AI parse fail (before fallback): job `2aea74fa-...`
- AI path reaches YouTube after fixes: job `73b33c5a-...` → channel limit
- Non-AI path: job `4dfd489a-...` → channel limit
