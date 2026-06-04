from collections.abc import Callable
from pathlib import Path

import yt_dlp

_IMAGE_EXTS = {".jpg", ".jpeg", ".webp", ".png"}


def download(
    url: str,
    output_dir: Path,
    on_progress: Callable[[float | None], None] | None = None,
) -> tuple[dict, Path, Path | None]:
    def hook(d: dict) -> None:
        if on_progress is None:
            return
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            downloaded = d.get("downloaded_bytes", 0)
            on_progress((downloaded / total * 100) if total else None)
        elif d["status"] == "finished":
            on_progress(100.0)

    def pp_hook(d: dict) -> None:
        # Signal indeterminate progress during postprocessing (stream merge, etc.)
        if on_progress is not None and d.get("status") == "started":
            on_progress(None)

    ydl_opts = {
        "format": "bestvideo+bestaudio/best",
        "outtmpl": str(output_dir / "video.%(ext)s"),
        "writethumbnail": True,
        "quiet": True,
        "no_warnings": True,
        "progress_hooks": [hook],
        "postprocessor_hooks": [pp_hook],
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)

    files = [
        f for f in output_dir.iterdir()
        if f.is_file() and f.suffix not in {".part", ".ytdl"}
    ]
    if not files:
        raise RuntimeError("Download produced no output file")

    video_files = [f for f in files if f.suffix.lower() not in _IMAGE_EXTS]
    thumb_files = [f for f in files if f.suffix.lower() in _IMAGE_EXTS]

    if not video_files:
        raise RuntimeError("Download produced no video file")

    return (
        info,
        max(video_files, key=lambda f: f.stat().st_size),
        thumb_files[0] if thumb_files else None,
    )
