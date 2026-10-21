"""Download a link, hold it in RAM, and reply with a URL Discord will unfurl.

Nothing is attached and nothing is written to the database: the bytes sit in
`bot.store.MemoryStore` for a few minutes, which is as long as Discord needs to
fetch the video and build its embed. The reply is a bare URL so Discord
actually unfurls it - wrapping it in `<>` or markdown suppresses the embed,
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
    """(width, height, duration_seconds) - og:video:width/height matter.

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
        log.info("skip %s - %.1fMB still over the %dMB item cap",
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
            log.info("skip %s - %.1fMB after mp4 normalisation", url, size / 1048576)
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


class _Ack:
    """However we said "working on it", and how to take it back.

    A reaction leaves nothing behind once removed, which is tidier, but it
    needs Add Reactions on the channel and is easy to miss. A message is
    unmissable but appears and disappears. Neither affects how fast Discord
    unfurls the link afterwards: a bot cannot set an embed's video field, so
    the player only ever arrives on Discord's own schedule.
    """

    def __init__(self, settings: BotSettings, message: discord.Message) -> None:
        self._settings = settings
        self._message = message
        self._note: discord.Message | None = None
        self._reacted = False

    async def start(self) -> None:
        mode = self._settings.ack_mode
        if mode == "none":
            return
        if mode == "reaction" and await self._react(self._settings.ack_emoji):
            self._reacted = True
            return
        if mode == "reaction":
            log.info("no Add Reactions here; acknowledging with a message instead")
        await self._post()

    async def done(self) -> None:
        """The link has landed, so withdraw the acknowledgement."""
        if self._reacted:
            if not self._settings.ack_keep_reaction:
                await self._unreact(self._settings.ack_emoji)
        elif self._note is not None:
            try:
                await self._note.delete()
            except discord.HTTPException:
                # Deleting its own message needs no permission, so this is just
                # a hiccup; the note is cosmetic either way.
                log.warning("could not delete the acknowledgement")

    async def failed(self) -> None:
        """Say so, rather than going quiet after promising to work on it."""
        if self._reacted:
            await self._unreact(self._settings.ack_emoji)
            await self._react(self._settings.ack_failed_emoji)
        elif self._note is not None:
            try:
                # Edited, not replaced: this text has no link for Discord to
                # unfurl, so there is nothing lost by editing in place.
                await self._note.edit(content=self._settings.ack_failed_text)
            except discord.HTTPException:
                log.warning("could not update the acknowledgement after a failure")

    async def _post(self) -> None:
        try:
            self._note = await self._message.reply(
                self._settings.ack_text, mention_author=False
            )
        except discord.HTTPException:
            # Not worth abandoning the job over; the link is the payload.
            log.warning("could not post the acknowledgement")

    async def _react(self, emoji: str) -> bool:
        try:
            await self._message.add_reaction(emoji)
            return True
        except discord.HTTPException:
            return False

    async def _unreact(self, emoji: str) -> None:
        me = self._message.guild.me if self._message.guild else None
        if me is None:
            return
        try:
            await self._message.remove_reaction(emoji, me)
        except discord.HTTPException:
            log.warning("could not remove the acknowledgement reaction")


async def _warmup_target(
    client: discord.Client, message: discord.Message, settings: BotSettings
):
    """Somewhere to post the link that nobody is looking at.

    A configured channel wins. Otherwise the application owner's DM, which
    needs no setup, is invisible to the server, and is a channel we can post in
    and delete from. A bot can DM a user it shares a guild with, which the
    owner of a bot in your own server is.
    """
    if settings.warmup_channel_id:
        channel = (
            message.guild.get_channel(settings.warmup_channel_id)
            if message.guild is not None
            else None
        )
        if channel is None:
            log.warning("warmup channel %s not found in this guild",
                        settings.warmup_channel_id)
        return channel

    try:
        app = client.application or await client.application_info()
        owner = app.owner or (app.team.owner if app.team else None)
        if owner is None:
            log.warning("could not identify the application owner to warm against")
            return None
        return owner.dm_channel or await owner.create_dm()
    except discord.HTTPException:
        log.warning("could not open the owner DM for warming")
        return None


async def _warm(
    client: discord.Client,
    message: discord.Message,
    key: str,
    link: str,
    settings: BotSettings,
    store: MemoryStore,
) -> None:
    """Get Discord to fetch the video before the link is posted publicly.

    Posting a link is what makes Discord fetch it, so the player can only ever
    arrive after the message. Posting it somewhere throwaway first means that
    by the time it goes out for real, Discord has already built the embed and
    should serve it from its per-URL cache.

    The wait is on our own byte accounting rather than a timer: we serve the
    file, so `store.is_complete` is first-hand knowledge that Discord finished
    pulling it. The settle pause afterwards is for Discord's own processing,
    which we cannot see.

    Best effort throughout. A missing channel, a failed post or a timeout all
    just mean the link goes out unwarmed, which is the old behaviour.
    """
    channel = await _warmup_target(client, message, settings)
    if channel is None:
        log.info("nowhere to warm against; posting unwarmed")
        return

    started = time.monotonic()
    try:
        note = await channel.send(link)
    except discord.HTTPException:
        log.warning("could not post to the warmup channel; posting unwarmed")
        return

    try:
        deadline = started + settings.warmup_timeout_seconds
        while time.monotonic() < deadline:
            if store.is_complete(key):
                waited = time.monotonic() - started
                log.info("warm %s: discord pulled the whole file in %.1fs, "
                         "settling %.1fs", key, waited, settings.warmup_settle_seconds)
                await asyncio.sleep(settings.warmup_settle_seconds)
                return
            await asyncio.sleep(0.25)
        log.info("warm %s: timed out after %.1fs with %d of %d bytes served; "
                 "posting anyway",
                 key, settings.warmup_timeout_seconds,
                 getattr(store.get(key), "served_bytes", 0),
                 getattr(store.get(key), "size", 0))
    finally:
        try:
            await note.delete()
        except discord.HTTPException:
            log.warning("could not delete the warmup message in #%s",
                        getattr(channel, "name", "?"))


async def rehost(
    client: discord.Client,
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
        log.info("skip %s - already in flight", url)
        return False
    _inflight.add(guard)

    started = time.monotonic()
    tmp = Path(tempfile.mkdtemp(dir=settings.bot_temp_dir or None))
    ack = _Ack(settings, message)
    try:
        await ack.start()

        # Keeps the typing indicator alive for the whole download, which is
        # free and needs no permission beyond posting.
        async with message.channel.typing():
            item = await asyncio.to_thread(_prepare, url, tmp, settings)

        if item is None:
            await ack.failed()
            return False

        store.put(key, item)
        link = f"{base_url.rstrip('/')}/d/{key}"

        if settings.warmup_enabled:
            await _warm(client, message, key, link, settings, store)

        # The URL goes first and stays bare on its own line: Discord unfurls a
        # plain URL in a message that has other text, but not one wrapped in
        # <> or markdown, and the embed is the entire point.
        body = link
        if settings.link_note:
            body = f"{link}\n{settings.link_note}"
        await message.reply(body, mention_author=False)
        await ack.done()
        log.info("posted %s as %s (%.1fMB, %sx%s, held %ds, %.1fs end to end; "
                 "store now %d item(s)/%.1fMB)",
                 url, link, item.size / 1048576, item.width, item.height,
                 settings.link_ttl_seconds, time.monotonic() - started,
                 len(store), store.total_bytes() / 1048576)
        return True

    except Exception:
        log.exception("failed to rehost %s", url)
        await ack.failed()
        return False
    finally:
        _inflight.discard(guard)
        shutil.rmtree(tmp, ignore_errors=True)
