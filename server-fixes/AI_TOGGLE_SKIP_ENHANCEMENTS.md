# Enable AI gates video enhancements

## Rule
Controls → **Enable AI** now controls both AI fix/Ollama **and** the ffmpeg enhancement step (subscribe overlay / audio replace / content-safety processing).

| Enable AI | Clip & Upload | Upload Schedule |
|-----------|---------------|-----------------|
| **Off** | Download → publish immediately (no enhancements) | Download → publish immediately (no enhancements) |
| **On** | AI check/fix path + enhancements via `process_video_for_upload` | Enhancements via `process_video_for_upload`, then publish |

## Files
- `clipper/services/video_processing.py` — hard gate: return raw filepath if AI off
- `clipper/services/pipeline.py` — `run_simple_clip_upload_job` skips enhancing stage
- `clipper/services/schedule_runner.py` — schedule only enhances when AI on
- `templates/clipper/panel.html` — tooltip updated

## Deploy
```bash
pkill -HUP -f "gunicorn config.wsgi"
# schedule timer loads code each run; no timer restart required
```
