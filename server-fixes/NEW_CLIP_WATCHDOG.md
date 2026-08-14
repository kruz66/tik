# New-clip watchdog (live)

## Behavior
- Watched sources are checked **every 1 minute** (`tiktok-cliper-schedule.timer`).
- On first watch (or deploy), each source gets a **baseline** of currently listed video IDs.
- Only video IDs that appear **after** that baseline are downloaded/uploaded.
- Older clips (hours/days ago that were already on the channel listing) are **not** uploaded.
- Global dedupe by `source_video_id` across schedule records, queue, seen IDs, and Clip Panel uploads.
- Failed queue retries only within `SCHEDULE_WATCH_MAX_AGE_HOURS` (default 6).
- Feeder `auto_queue_priority_clips` dump disabled (it was re-posting old clips).

## Settings
- `SCHEDULE_CHECK_MINUTES=1`
- `SCHEDULE_FETCH_LIMIT=20`
- `SCHEDULE_WATCH_MAX_AGE_HOURS=6`
- `SCHEDULE_SEEN_IDS_LIMIT=800`

## Models
`ScheduledSource.baseline_seeded_at`, `ScheduledSource.seen_video_ids`
