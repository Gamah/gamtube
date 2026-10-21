"""Download a link, hold it in RAM, and reply with a URL Discord will unfurl.

Nothing is attached and nothing is written to the database: the bytes sit in
`bot.store.MemoryStore` for a few minutes, which is as long as Discord needs to
fetch the video and build its embed. The reply is a bare URL so Discord
actually unfurls it — wrapping it in `<>` or markdown suppresses the embed,
which is the one thing we need.
"""

import asyncio
import logging
import shutil
import tempfile
import time
from pathlib import Path

import discord

from bot.config import BotSettings
from bot.store import Item, MemoryStore
from core.downloader import download
from core.ids import short_id_for
from core.titles import clean_title
from core.transcoder import _probe, transcode

log = logging.getLogger("gamtube.bot")

# Work already running, keyed "{channel_id}:{short_id}", so the same link
# pasted twice in quick succession is downloaded once. Process-local by design.
_inflight: set[str] = set()


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


def _dimensions(path: Path) -> tuple[int | None, int | None, int | None]:
    """(width, height, duration_seconds) — og:video:width/height matter.

    Without dimensions Discord sizes the player badly or degrades the embed to
    a plain card, which is the failure this whole bot exists to avoid.
    """
    try:
        probe = _probe(path)
    except Exception:
        return None, None, None
    stream = next(
        (s for s in probe.get("streams", []) if s.get("codec_type") == "video"), {}
    )
    duration = probe.get("format", {}).get("duration") or stream.get("duration")
    try:
        seconds = int(float(duration)) if duration else None
    except (TypeError, ValueError):
        seconds = None
    return stream.get("width"), stream.get("height"), seconds


def _prepare(url: str, tmp: Path, settings: BotSettings) -> Item | None:
    """Blocking half: download, normalise, measure. Runs in a worker thread.

    Each stage is timed and logged: the open question is whether download plus
    normalisation fits inside the window before someone scrolls past, and
    that only shows up as numbers from a real guild.
    """
    cap = settings.max_item_mb * 1024 * 1024
    t0 = time.monotonic()
    info, src, _ = download(
        url, tmp, None, fmt=_format_selector(cap), writethumbnail=False
    )
    size = src.stat().st_size
    log.info("download %s in %.1fs -> %s %.1fMB (extractor=%s)",
             url, time.monotonic() - t0, src.suffix, size / 1048576,
             info.get("extractor_key") or info.get("extractor"))

    if size > cap and settings.shrink_enabled:
        shrunk = tmp / "shrunk.mp4"
        log.info("%s is %.1fMB, over the %dMB item cap; shrinking",
                 url, size / 1048576, settings.max_item_mb)
        t1 = time.monotonic()
        transcode(src, shrunk, None,
                  crf=settings.shrink_crf, max_height=settings.shrink_max_height)
        if shrunk.exists() and shrunk.stat().st_size < size:
            log.info("shrink %s in %.1fs: %.1fMB -> %.1fMB",
                     url, time.monotonic() - t1, size / 1048576,
                     shrunk.stat().st_size / 1048576)
            src, size = shrunk, shrunk.stat().st_size
        else:
            log.info("shrink %s in %.1fs made it no smaller; keeping the original",
                     url, time.monotonic() - t1)

    if size > cap:
        log.info("skip %s — %.1fMB still over the %dMB item cap",
                 url, size / 1048576, settings.max_item_mb)
        return None

    if settings.force_mp4 and src.suffix.lower() != ".mp4":
        # Discord's embed player is fed the URL directly; mp4/H.264 is the only
        # container it plays everywhere, iOS included.
        mp4 = tmp / "forced.mp4"
        log.info("%s arrived as %s; normalising to mp4", url, src.suffix)
        t2 = time.monotonic()
        transcode(src, mp4, None)
        src = mp4
        size = src.stat().st_size
        log.info("normalise %s in %.1fs -> %.1fMB",
                 url, time.monotonic() - t2, size / 1048576)
        if size > cap:
            log.info("skip %s — %.1fMB after mp4 normalisation", url, size / 1048576)
            return None

    width, height, duration = _dimensions(src)
    if width is None or height is None:
        # og:video:width/height drive the player size; missing them is the
        # likeliest cause of an embed that renders as a plain card.
        log.warning("%s has no probe dimensions; the embed may degrade", url)
    log.info("prepared %s: %.1fMB %sx%s %ss total %.1fs",
             url, size / 1048576, width, height, duration,
             time.monotonic() - t0)
    return Item(
        data=src.read_bytes(),
        content_type="video/mp4" if src.suffix.lower() == ".mp4" else "video/webm",
        title=clean_title(info.get("title")),
        width=width,
        height=height,
        duration=duration,
        source_url=url,
    )


async def _drop(ack: "discord.Message | None") -> None:
    """Remove the acknowledgement once the link has landed."""
    if ack is None:
        return
    try:
        await ack.delete()
    except discord.HTTPException:
        # Deleting its own message needs no permission, so this is just a
        # network hiccup; the note is cosmetic either way.
        log.warning("could not delete the acknowledgement")


async def _fail(ack: "discord.Message | None", settings: BotSettings) -> None:
    """Turn the acknowledgement into a short explanation.

    Edited rather than replaced because it carries no link, so there is nothing
    for Discord to unfurl and nothing lost by editing. Silence after saying
    "working on it" would be worse than a one-line no.
    """
    if ack is None:
        return
    try:
        await ack.edit(content=settings.ack_failed_text)
    except discord.HTTPException:
        log.warning("could not update the acknowledgement after a failure")


async def rehost(
    message: discord.Message,
    url: str,
    settings: BotSettings,
    store: MemoryStore,
    base_url: str,
) -> bool:
    """Download `url`, stash it, and reply with its link. True if we posted."""
    key = short_id_for(url)
    guard = f"{message.channel.id}:{key}"
    if guard in _inflight:
        log.info("skip %s — already in flight", url)
        return False
    _inflight.add(guard)

    started = time.monotonic()
    tmp = Path(tempfile.mkdtemp(dir=settings.bot_temp_dir or None))
    ack = None
    try:
        if settings.ack_enabled:
            try:
                ack = await message.reply(settings.ack_text, mention_author=False)
            except discord.HTTPException:
                # Not worth abandoning the job over; the link is the payload.
                log.warning("could not post the acknowledgement for %s", url)

        # Keeps the typing indicator alive for the whole download, which is
        # free and needs no permission beyond posting.
        async with message.channel.typing():
            item = await asyncio.to_thread(_prepare, url, tmp, settings)

        if item is None:
            await _fail(ack, settings)
            return False

        store.put(key, item)
        link = f"{base_url.rstrip('/')}/d/{key}"
        await message.reply(link, mention_author=False)
        await _drop(ack)
        log.info("posted %s as %s (%.1fMB, %sx%s, held %ds, %.1fs end to end; "
                 "store now %d item(s)/%.1fMB)",
                 url, link, item.size / 1048576, item.width, item.height,
                 settings.link_ttl_seconds, time.monotonic() - started,
                 len(store), store.total_bytes() / 1048576)
        return True

    except Exception:
        log.exception("failed to rehost %s", url)
        await _fail(ack, settings)
        return False
    finally:
        _inflight.discard(guard)
        shutil.rmtree(tmp, ignore_errors=True)
