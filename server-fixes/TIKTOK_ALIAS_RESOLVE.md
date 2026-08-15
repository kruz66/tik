# TikTok vanity alias resolve

## Problem

Some TikTok handles fail profile listing in yt-dlp with:

`Unable to extract secondary user ID` / UI: **No data returned for this TikTok account.**

Example: `@senyedit3` (vanity / embedding-disabled) is the same creator as API uniqueId `senyedit09`.

## Fix

When a profile playlist fetch returns no data, `clipper/services/tiktok.py` now:

1. Searches for public videos that still use `@requested_handle` in the URL
   - DuckDuckGo HTML (when not bot-blocked)
   - Bright Data SERP (if `BRIGHTDATA_API_KEY` + `BRIGHTDATA_SERP_ZONE` are set)
   - Wayback Machine CDX index (reliable from datacenter IPs)
2. Probes one of those videos with yt-dlp to read `channel_id` (secUid) + canonical `uploader`
3. Re-fetches the playlist via `tiktokuser:{channel_id}` or `@canonical_uniqueId`
4. Caches the mapping for 7 days
5. Also learns the mapping when a single vanity video URL is fetched successfully

Returned payload includes:

- `alias_resolved`
- `requested_username`
- `resolved_username` / `username` (canonical)
- `channel_id`
