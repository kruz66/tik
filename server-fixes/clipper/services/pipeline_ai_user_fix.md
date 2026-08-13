# Fix: AI clip-upload `name 'user' is not defined`

## Symptom

`/api/clip-upload/` with AI enabled fails after frame review:

```text
[YouTube] name 'user' is not defined
```

Non-AI clip-upload reaches the YouTube API (then may hit channel daily limit).

## Root cause

`start_clip_upload_job` starts the worker thread **without** passing `user`:

```python
thread = threading.Thread(
    target=target,  # run_clip_upload_job if AI else run_simple_clip_upload_job
    args=(str(job.id), video_items, username, credentials_json, destinations),
    daemon=True,
)
```

`run_simple_clip_upload_job` already binds `user = job.user`.
`run_clip_upload_job` appears to call `publish_clip(user, ...)` (or similar) **without** binding `user` first. `format_publish_error` / YouTube except handlers wrap the NameError as `[YouTube] ...`.

## Patch (on server `/root/tiktok-cliper/clipper/services/pipeline.py`)

In `run_clip_upload_job`, immediately after loading the job:

```python
def run_clip_upload_job(
    job_id: str,
    video_items: list[dict],
    username: str,
    credentials_json: str,
    destinations=None,
):
    job = UploadJob.objects.get(id=job_id)
    user = job.user  # REQUIRED — thread args omit user
    # ... rest of AI review + publish_clip(user, ...) ...
```

Optional hardening in `start_clip_upload_job`:

```python
args=(str(job.id), video_items, username, credentials_json, destinations, user),
```

and add a trailing `user=None` parameter to both workers, preferring `user or job.user`.

## Deploy

```bash
# after editing pipeline.py
pkill -HUP -f "gunicorn config.wsgi"
# or restart the gunicorn unit used by tiktok-cliper
```

## Verify

1. Dashboard → Controls → AI features ON
2. Clip & Upload 1 YouTube Short with destination YouTube only
3. Job must not fail with `name 'user' is not defined`
4. If it fails with channel daily upload limit, that is a separate Google unverified-app cap (not this bug)
