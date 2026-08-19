# TikTok fetch + schedule upload fix (Chrome impersonation)

## Root causes

1. **Fetch Videos (“No data returned for this TikTok account.”)**  
   TikTok blocks yt-dlp’s default TLS fingerprint. Profile listing for handles like `@onlyclipinn` fails unless yt-dlp uses **Chrome impersonation** via `curl_cffi`.

2. **Schedule not uploading**  
   Two stacked issues:
   - Same TikTok fetch failure, then DuckDuckGo alias lookups that **timed out for minutes** and held the schedule flock lock.
   - After fetch recovered, `check_scheduled_source` called `_remember_seen_ids()` on **brand-new** video IDs *before* upload, so `process_queue_items` immediately skipped them as “already seen.”

## Fix

- Always pass `impersonate=chrome` (`ImpersonateTarget`) in `_base_ydl_opts()`.
- Prefer profile-HTML `secUid` scrape, then Wayback / Bright Data; DuckDuckGo last with short timeout.
- Pin `curl_cffi==0.13.0` (newer versions break yt-dlp impersonate).
- Only mark non-new listing IDs as seen before upload; mark new IDs after successful upload.
- `process_queue_items` excludes the current pending batch from the “already seen” set.

## Deploy

```bash
scp server-fixes/clipper/services/tiktok.py root@HOST:/root/tiktok-cliper/clipper/services/tiktok.py
scp server-fixes/clipper/services/schedule_runner.py root@HOST:/root/tiktok-cliper/clipper/services/schedule_runner.py
# ensure curl_cffi==0.13.0 in venv
# reload gunicorn workers; clear stuck schedule lock holder if needed
```

## Verified (live)

- `POST /api/fetch-videos/` for `onlyclipinn` → 100 videos (HTTP 200)
- Alias resolves: `kickclipper_` → `liuclipper`, `senyedit3` → `senyedit09`
- `check_user_schedules(wishfox)` → **Downloaded and uploaded 14 video(s) to YouTube**
