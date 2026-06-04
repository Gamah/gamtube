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
) -> Path:
    probe = _probe(input_path)
    streams = probe.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)

    if video and video.get("codec_name") == "h264" and input_path.suffix.lower() == ".mp4":
        codec_args = ["-c", "copy"]
    else:
        codec_args = [
            "-c:v", "libx264", "-crf", "23", "-preset", "superfast",
            "-c:a", "aac", "-b:a", "128k",
        ]

    if on_progress is None:
        subprocess.run(
            ["ffmpeg", "-i", str(input_path), *codec_args, str(output_path), "-y"],
            check=True, capture_output=True,
        )
        return output_path

    duration_us = _duration_us(probe)
    if not duration_us:
        on_progress(None)  # indeterminate — duration unknown, shimmer until done

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
