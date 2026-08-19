# TikTok fetch + schedule upload fix (Chrome impersonation)

## Root causes

1. **Fetch Videos (“No data returned for this TikTok account.”)**  
   TikTok blocks yt-dlp’s default TLS fingerprint. Profile listing for handles like `@onlyclipinn` fails unless yt-dlp uses **Chrome impersonation** via `curl_cffi`.

2. **Schedule not uploading**  
   The schedule watchdog (`check_upload_schedules`) uses the same TikTok fetch path. When listing failed, it fell into **DuckDuckGo alias discovery**, which often **times out for minutes** from the server IP and held the schedule flock lock — so new clips never queued/uploaded.

## Fix

- Always pass `impersonate=chrome` (`ImpersonateTarget`) in `_base_ydl_opts()`.
- Prefer profile-HTML `secUid` scrape, then Wayback, then Bright Data; DuckDuckGo last with short timeout.
- Pin `curl_cffi==0.13.0` (newer versions break yt-dlp impersonate).

## Deploy

```bash
scp server-fixes/clipper/services/tiktok.py root@HOST:/root/tiktok-cliper/clipper/services/tiktok.py
# ensure curl_cffi==0.13.0 in venv
systemctl restart gunicorn-or-equivalent
# clear stuck schedule lock holder if needed, then let timer run
```
