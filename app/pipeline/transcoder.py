import json
import subprocess
from pathlib import Path


def _probe(path: Path) -> dict:
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", str(path)],
        capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout)


def transcode(input_path: Path, output_path: Path) -> Path:
    streams = _probe(input_path).get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)

    if video and video.get("codec_name") == "h264" and input_path.suffix.lower() == ".mp4":
        codec_args = ["-c", "copy"]
    else:
        codec_args = [
            "-c:v", "libx264", "-crf", "23", "-preset", "fast",
            "-c:a", "aac", "-b:a", "128k",
        ]

    subprocess.run(
        ["ffmpeg", "-i", str(input_path), *codec_args, str(output_path), "-y"],
        check=True, capture_output=True,
    )
    return output_path
