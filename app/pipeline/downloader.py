from pathlib import Path
import yt_dlp


def download(url: str, output_dir: Path) -> tuple[dict, Path]:
    ydl_opts = {
        "format": "bestvideo+bestaudio/best",
        "outtmpl": str(output_dir / "video.%(ext)s"),
        "writethumbnail": False,
        "quiet": True,
        "no_warnings": True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)

    files = [
        f for f in output_dir.iterdir()
        if f.is_file() and f.suffix not in {".part", ".ytdl"}
    ]
    if not files:
        raise RuntimeError("Download produced no output file")

    return info, max(files, key=lambda f: f.stat().st_size)
