"""Download a link and reply with it as a Discord attachment.

Nothing here touches the web app's database or storage backend: the file lives
in a temp dir for as long as the upload takes and is then deleted. Once the
attachment is posted, Discord holds the only copy.
"""

import asyncio
import logging
import shutil
import tempfile
from pathlib import Path

import discord

from bot.config import BotSettings
from core.downloader import download
from core.ids import short_id_for
from core.titles import clean_title, safe_filename
from core.transcoder import transcode

log = logging.getLogger("gamtube.bot")

# Discord's base attachment cap, used only for DMs, where there is no guild to
# read filesize_limit from. Conservative on purpose; override with MAX_UPLOAD_MB.
DM_UPLOAD_LIMIT = 10 * 1024 * 1024

# Work already running, keyed "{channel_id}:{short_id}", so the same link
# pasted twice in quick succession is downloaded once. Process-local by design.
_inflight: set[str] = set()


def upload_limit(message: discord.Message, settings: BotSettings) -> int:
    if settings.max_upload_mb > 0:
        return settings.max_upload_mb * 1024 * 1024
    guild = message.guild
    return guild.filesize_limit if guild else DM_UPLOAD_LIMIT


def _format_selector(cap: int) -> str:
    """Prefer H.264/AAC that already fits, so the common case needs no ffmpeg.

    `filesize<?` keeps formats whose size yt-dlp doesn't know rather than
    discarding them, so a stream with no reported size is still a candidate.
    """
    return (
        f"bv*[vcodec^=avc1][filesize<?{cap}]+ba[acodec^=mp4a]/"
        f"b[vcodec^=avc1][filesize<?{cap}]/"
        f"bv*[filesize<?{cap}]+ba/b[filesize<?{cap}]/"
        "bv*+ba/b"
    )


async def rehost(message: discord.Message, url: str, settings: BotSettings) -> bool:
    """Download `url` and reply to `message` with it. True if something was posted."""
    key = f"{message.channel.id}:{short_id_for(url)}"
    if key in _inflight:
        log.info("skip %s — already in flight", url)
        return False
    _inflight.add(key)

    cap = upload_limit(message, settings)
    tmp = Path(tempfile.mkdtemp(dir=settings.bot_temp_dir or None))
    try:
        info, src, _ = await asyncio.to_thread(
            download, url, tmp, None,
            fmt=_format_selector(cap),
            writethumbnail=False,
        )
        size = src.stat().st_size

        if size > cap and settings.shrink_enabled:
            shrunk = tmp / "shrunk.mp4"
            log.info("%s is %.1fMB over cap; shrinking", url, (size - cap) / 1048576)
            await asyncio.to_thread(
                transcode, src, shrunk, None,
                crf=settings.shrink_crf,
                max_height=settings.shrink_max_height,
            )
            # A shrink that came out bigger is no shrink at all.
            if shrunk.exists() and shrunk.stat().st_size < size:
                src = shrunk
                size = src.stat().st_size

        if size > cap:
            log.info("skip %s — %.1fMB exceeds %.1fMB cap", url, size / 1048576, cap / 1048576)
            return False

        name = safe_filename(clean_title(info.get("title")), src.suffix)
        await message.reply(
            file=discord.File(src, filename=name),
            mention_author=False,
        )
        log.info("posted %s as %s (%.1fMB)", url, name, size / 1048576)
        return True

    except Exception:
        log.exception("failed to rehost %s", url)
        return False
    finally:
        _inflight.discard(key)
        shutil.rmtree(tmp, ignore_errors=True)
