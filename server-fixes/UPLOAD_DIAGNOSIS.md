# YouTube upload diagnosis (2026-08-13)

## OAuth / pool status (wishfox)
- connected: true
- channel: Code Matrix (UC0u9r_tzZk9w91MhRZE8TtQ)
- pool_linked: 9/9 (complete)
- zernio: false

## Live upload tests

### Test A — AI clip-upload (job 43ec9089-...)
- Path: /api/clip-upload/ with ai_features_enabled=true
- Progress: "AI reviewing frames for policy issues..."
- Result: FAILED
- Error: `[YouTube] name 'user' is not defined`
- Cause: Python NameError in AI worker `run_clip_upload_job` (likely missing `user = job.user`)

### Test B — Non-AI clip-upload (job 6b85909a-...)
- Path: /api/clip-upload/ with ai_features_enabled=false
- Progress: Downloading → Applying enhancements → upload
- Result: FAILED
- Error: `[YouTube] YouTube channel daily upload limit reached for this Google account. Unverified OAuth apps can only upload a few videos per day until Google approves verification.`
- Cause: Google YouTube API `uploadLimitExceeded` (channel daily cap for Testing/unverified OAuth clients). Rotating the 9 GCP client IDs does not bypass this — same channel + same Google account.

## Schedule confirmation
- Source DDGLiveClips / thirstyclips: same channel-limit message
- @capbygi earlier: claimed 5 YouTube uploads then TikTok inbox full

## What will unblock a successful test upload
1. Wait for YouTube channel daily upload limit reset (typically midnight Pacific), OR
2. Link/upload to a different YouTube channel/Google account, OR
3. Complete Google OAuth app verification (removes unverified daily upload cap)
4. Plus: fix AI NameError on server (`pipeline.py` run_clip_upload_job) — needs SSH to `/root/tiktok-cliper`

## SSH
Cloud agent key missing. New pubkey to authorize on server:
`ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIHr4sm9XGlWqvxvGa6j2JdgPLCPI3TbS79FjQ5FycCLE cursor-agent-youtube-debug-20260813`
