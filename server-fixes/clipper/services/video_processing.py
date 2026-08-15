import logging
import subprocess
import tempfile
from pathlib import Path

import requests
from django.conf import settings
from PIL import Image, ImageDraw

from clipper.models import YouTubeConnection
from clipper.services.ffmpeg_encode import (
    high_quality_audio_encode_args,
    high_quality_video_encode_args,
)
from clipper.services.content_safety import ContentSafetyError, check_content_safety, should_replace_audio

logger = logging.getLogger(__name__)

ASSETS_DIR = Path(settings.BASE_DIR) / "clipper" / "assets"
MUSIC_DIR = ASSETS_DIR / "music"
OVERLAY_SECONDS = 3.0

AUDIO_MOODS = {
    "calm": "calm.mp3",
    "upbeat": "upbeat.mp3",
    "hype": "hype.mp3",
}


def _get_connection(user) -> YouTubeConnection | None:
    if not user or not user.is_authenticated:
        return None
    return YouTubeConnection.objects.filter(user=user).first()


def get_processing_options(user) -> dict:
    connection = _get_connection(user)
    if not connection:
        return {
            "enabled": True,
            "subscribe_overlay": True,
            "audio_replace": False,
            "content_safety": True,
            "audio_mood": "upbeat",
        }
    return {
        "enabled": connection.video_processing_enabled,
        "subscribe_overlay": connection.subscribe_overlay_enabled,
        "audio_replace": connection.audio_replace_enabled,
        "content_safety": connection.content_safety_enabled,
        "audio_mood": connection.audio_mood or "upbeat",
    }


def process_video_for_upload(
    filepath: str,
    user,
    *,
    title: str = "",
    description: str = "",
    copyright_audio_detected: bool = False,
    preserve_audio: bool = True,
    destinations: list[str] | None = None,
) -> str:
    # Enhancements (subscribe overlay, audio replace, safety checks) only run
    # when Controls → Enable AI is on. Otherwise return the raw download path.
    from clipper.services.posting_preferences import ai_features_enabled

    if user is None or not ai_features_enabled(user):
        return filepath

    options = get_processing_options(user)
    if not options["enabled"]:
        return filepath

    if options["content_safety"]:
        check_content_safety(title, description)

    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"Video file not found: {filepath}")

    working = path
    cleanup: list[Path] = []
    normalized_destinations = [str(item).strip().lower() for item in (destinations or []) if str(item).strip()]
    skip_youtube_overlay = bool(normalized_destinations) and "youtube" not in normalized_destinations

    # Never touch original audio unless the caller explicitly opts out.
    replace_audio = (
        not preserve_audio
        and not copyright_audio_detected
        and options["audio_replace"]
        and should_replace_audio(title, description)
    )

    try:
        if replace_audio:
            audio_out = path.with_name(f"{path.stem}_audio.mp4")
            _replace_audio_track(working, audio_out, options["audio_mood"])
            if working != path:
                cleanup.append(working)
            working = audio_out

        if options["subscribe_overlay"] and not skip_youtube_overlay:
            connection = _get_connection(user)
            channel_title = (connection.channel_title if connection else "") or "Subscribe"
            avatar_url = (connection.channel_thumbnail if connection else "") or ""
            overlay_out = path.with_name(f"{path.stem}_overlay.mp4")
            _apply_subscribe_overlay(
                working,
                overlay_out,
                channel_title=channel_title,
                avatar_url=avatar_url,
            )
            if working != path:
                cleanup.append(working)
            working = overlay_out

        if working != path:
            path.unlink(missing_ok=True)
            working.replace(path)

        return str(path)
    finally:
        for temp in cleanup:
            temp.unlink(missing_ok=True)


def _run(cmd: list[str], timeout: int = 600) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "ffmpeg command failed")


def _probe_duration(filepath: Path) -> float:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(filepath),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=True)
    return max(float(result.stdout.strip()), 1.0)


def _music_path(mood: str) -> Path:
    filename = AUDIO_MOODS.get(mood, AUDIO_MOODS["upbeat"])
    path = MUSIC_DIR / filename
    if not path.exists():
        raise FileNotFoundError(f"Bundled music track not found: {path}")
    return path


def _replace_audio_track(input_path: Path, output_path: Path, mood: str) -> None:
    duration = _probe_duration(input_path)
    music = _music_path(mood)
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-stream_loop",
        "-1",
        "-i",
        str(music),
        "-filter_complex",
        (
            f"[1:a]atrim=0:{duration},asetpts=PTS-STARTPTS,volume=0.55[aout]"
        ),
        "-map",
        "0:v:0",
        "-map",
        "[aout]",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "256k",
        "-shortest",
        str(output_path),
    ]
    _run(cmd)
    logger.info("Replaced audio with royalty-free track (%s) for %s", mood, input_path.name)


def _download_avatar(url: str, dest: Path) -> Path | None:
    if not url:
        return None
    try:
        response = requests.get(url, timeout=20)
        response.raise_for_status()
        dest.write_bytes(response.content)
        return dest
    except Exception as exc:
        logger.warning("Could not download channel avatar: %s", exc)
        return None


def _render_subscribe_card(channel_title: str, avatar_path: Path | None, out_path: Path) -> None:
    width, height = 560, 180
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    panel_color = (15, 15, 15, 220)
    draw.rounded_rectangle((0, 0, width - 1, height - 1), radius=24, fill=panel_color)

    avatar_size = 96
    avatar_x, avatar_y = 28, (height - avatar_size) // 2
    if avatar_path and avatar_path.exists():
        avatar = Image.open(avatar_path).convert("RGBA").resize(
            (avatar_size, avatar_size), Image.Resampling.LANCZOS
        )
        mask = Image.new("L", (avatar_size, avatar_size), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, avatar_size, avatar_size), fill=255)
        img.paste(avatar, (avatar_x, avatar_y), mask)
    else:
        draw.ellipse(
            (avatar_x, avatar_y, avatar_x + avatar_size, avatar_y + avatar_size),
            fill=(60, 60, 60, 255),
        )

    text_x = avatar_x + avatar_size + 24
    title = (channel_title or "My Channel")[:28]
    draw.text((text_x, 42), title, fill=(255, 255, 255, 255))
    draw.rounded_rectangle(
        (text_x, 98, text_x + 190, 142),
        radius=18,
        fill=(255, 0, 0, 255),
    )
    draw.text((text_x + 34, 108), "SUBSCRIBE", fill=(255, 255, 255, 255))

    img.save(out_path)


def _apply_subscribe_overlay(
    input_path: Path,
    output_path: Path,
    *,
    channel_title: str,
    avatar_url: str,
) -> None:
    duration = _probe_duration(input_path)
    mid_start = max(0.5, (duration / 2) - (OVERLAY_SECONDS / 2))
    mid_end = min(duration - 0.5, mid_start + OVERLAY_SECONDS)
    end_start = max(0.5, duration - OVERLAY_SECONDS)
    end_end = duration

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        avatar_path = tmp_dir / "avatar.png"
        card_path = tmp_dir / "subscribe_card.png"
        _download_avatar(avatar_url, avatar_path)
        _render_subscribe_card(channel_title, avatar_path, card_path)

        enable_expr = (
            f"between(t,{mid_start:.3f},{mid_end:.3f})"
            f"+between(t,{end_start:.3f},{end_end:.3f})"
        )
        filter_complex = (
            f"[1:v]scale=560:-1,format=rgba,fade=t=in:st=0:d=0.35:alpha=1,"
            f"fade=t=out:st={OVERLAY_SECONDS - 0.35:.2f}:d=0.35:alpha=1[card];"
            f"[0:v][card]overlay=(W-w)/2:(H-h)/2:enable='{enable_expr}'[vout]"
        )

        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(input_path),
            "-i",
            str(card_path),
            "-filter_complex",
            filter_complex,
            "-map",
            "[vout]",
            "-map",
            "0:a?",
            *high_quality_video_encode_args(input_path=input_path),
            *high_quality_audio_encode_args(copy=True),
            str(output_path),
        ]
        _run(cmd, timeout=900)

    logger.info(
        "Applied subscribe overlay to %s (mid %.1fs, end %.1fs)",
        input_path.name,
        mid_start,
        end_start,
    )
