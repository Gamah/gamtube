import json
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path


def _probe(path: Path) -> dict:
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_streams", "-show_format", str(path)],
        capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout)


def _duration_us(probe: dict) -> float | None:
    for s in probe.get("streams", []):
        raw = s.get("duration")
        if raw:
            try:
                return float(raw) * 1_000_000
            except (ValueError, TypeError):
                pass
    # mkv and other containers store duration at format level
    raw = probe.get("format", {}).get("duration")
    if raw:
        try:
            return float(raw) * 1_000_000
        except (ValueError, TypeError):
            pass
    return None


def transcode(
    input_path: Path,
    output_path: Path,
    on_progress: Callable[[float | None], None] | None = None,
    *,
    crf: int = 23,
    max_height: int | None = None,
    force_encode: bool = False,
) -> Path:
    """Normalise to H.264/AAC mp4, stream-copying when the input already is.

    `crf` and `max_height` are the bot's shrink knobs: a higher crf and a
    downscale are what bring an over-cap clip under Discord's limit. Setting
    either away from the default rules out the stream-copy path, and
    `force_encode` rules it out directly, for an input whose container is
    already mp4/H.264 but whose pixel format or profile is not playable.

    `-movflags +faststart` is on both paths. It makes ffmpeg rewrite the file
    with the `moov` index before the media, which a player reading over ranged
    HTTP needs in order to start at all. On the copy path that is the only work
    done, so a plain remux is cheap.
    """
    probe = _probe(input_path)
    streams = probe.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)

    scale_args: list[str] = []
    if max_height and video and (video.get("height") or 0) > max_height:
        # -2 keeps the width even, which libx264 requires
        scale_args = ["-vf", f"scale=-2:{max_height}"]

    if (
        not force_encode
        and not scale_args
        and crf == 23
        and video
        and video.get("codec_name") == "h264"
        and input_path.suffix.lower() == ".mp4"
    ):
        codec_args = ["-c", "copy", "-movflags", "+faststart"]
    else:
        if not scale_args:
            # libx264 rejects an odd width or height; trunc to even rather
            # than failing on a source that has one.
            scale_args = ["-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2"]
        codec_args = [
            *scale_args,
            # yuv420p explicitly: libx264 otherwise keeps the input's pixel
            # format, and yuv444p or 10 bit output plays in VLC and nowhere
            # Discord's player or a browser can decode it.
            "-c:v", "libx264", "-crf", str(crf), "-preset", "superfast",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
        ]

    if on_progress is None:
        subprocess.run(
            ["ffmpeg", "-i", str(input_path), *codec_args, str(output_path), "-y"],
            check=True, capture_output=True,
        )
        return output_path

    duration_us = _duration_us(probe)
    if not duration_us:
        on_progress(None)  # indeterminate - duration unknown, shimmer until done

    proc = subprocess.Popen(
        [
            "ffmpeg", "-i", str(input_path), *codec_args,
            "-progress", "pipe:1", "-loglevel", "error",
            str(output_path), "-y",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    stderr_chunks: list[str] = []
    stderr_thread = threading.Thread(
        target=lambda: stderr_chunks.append(proc.stderr.read()),
        daemon=True,
    )
    stderr_thread.start()

    for line in proc.stdout:
        if duration_us and line.startswith("out_time_us="):
            try:
                out_us = int(line.strip().split("=", 1)[1])
                pct = max(0.0, min(out_us / duration_us * 100, 99.9))
                on_progress(pct)
            except (ValueError, IndexError):
                pass

    proc.wait()
    stderr_thread.join()

    if proc.returncode != 0:
        raise subprocess.CalledProcessError(
            proc.returncode, proc.args, stderr="".join(stderr_chunks)
        )

    on_progress(100.0)
    return output_path
