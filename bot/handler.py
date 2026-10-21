"""Download a link and give Discord something playable: an attachment, or a URL.

The attachment is the good outcome. Discord becomes the host, the player is
there the instant the message lands, and none of the link machinery is
involved: no store entry, no TTL, no warmup, no `/d/` route. The only reason
it is not the whole story is the guild's `filesize_limit`.

So anything that will not fit, and any refusal or throttle from Discord, falls
back to the link path: the bytes sit in `bot.store.MemoryStore` for a few
minutes, which is as long as Discord needs to fetch the video and build its
embed, and the reply is a bare URL so Discord actually unfurls it - wrapping it
in `<>` or markdown suppresses the embed, which is the one thing we need. The
fallback is total; a failed upload never leaves the user with nothing.

Either way the file goes through the compatibility gate first. A downloaded
mp4 is not trusted as-is: see `_normalise`.
"""

import asyncio
import io
import logging
import mmap
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import discord

from bot.config import BotSettings
from bot.store import Item, MemoryStore
from bot.upload import Outcome, plan as upload_plan
from core.downloader import download
from core.ids import short_id_for
from core.mp4 import Verdict, compat_verdict, top_level_boxes
from core.titles import clean_title, safe_filename
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


def _boxes(path: Path) -> list[tuple[str, int, int]]:
    """Top level mp4 boxes, read by mmap so a huge mdat is never loaded."""
    if path.stat().st_size == 0:
        return []
    with path.open("rb") as fh:
        with mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as mm:
            return top_level_boxes(mm)


_GATE_VERB = {"remux": "remuxed", "reencode": "re-encoded"}


def _normalise(url: str, src: Path, tmp: Path, settings: BotSettings) -> Path:
    """Make the file reliably playable, or leave it alone if it already is.

    A downloaded mp4 is not trusted as-is. yt-dlp hands back whatever the
    platform served, which means the `moov` index can sit after `mdat` - and
    then a player reading over ranged HTTP, which is exactly how `/d/{key}/f`
    is fetched, has audio or a first frame and a picture that never advances.
    The streams can be just as wrong: 10 bit or 4:2:2 H.264 and non-AAC audio
    both play locally and nowhere Discord's player can decode them.

    `core.mp4.compat_verdict` splits that into the two costs it deserves. A
    layout-only problem is a remux (`-c copy -movflags +faststart`); anything
    about the streams is a real re-encode. The gate runs before the upload
    decision, not just on the link fallback: Discord re-encodes attachments
    anyway, but shipping it a broken index is still not worth doing.
    """
    if src.suffix.lower() != ".mp4":
        if not settings.force_mp4:
            log.info("gate %s: left as %s, FORCE_MP4 is off", url, src.suffix)
            return src
        out = tmp / "gated.mp4"
        log.info("gate %s: re-encoding, it arrived as %s not mp4", url, src.suffix)
        t0 = time.monotonic()
        transcode(src, out, None)
        log.info("gate %s: re-encoded in %.1fs -> %.1fMB",
                 url, time.monotonic() - t0, out.stat().st_size / 1048576)
        return out

    try:
        verdict = compat_verdict(_probe(src), _boxes(src))
    except Exception:
        # Inspection failing is not a reason to serve something unplayable;
        # a remux is cheap and fixes the likeliest problem.
        log.warning("gate %s: could not inspect it, remuxing", url, exc_info=True)
        verdict = Verdict("remux", "inspection failed")

    if verdict.action == "ok":
        log.info("gate %s: already fine - %s", url, verdict.reason)
        return src

    out = tmp / f"gated-{verdict.action}.mp4"
    t0 = time.monotonic()
    transcode(src, out, None, force_encode=verdict.action == "reencode")
    log.info("gate %s: %s in %.1fs -> %.1fMB - %s",
             url, _GATE_VERB[verdict.action], time.monotonic() - t0,
             out.stat().st_size / 1048576, verdict.reason)
    return out


@dataclass
class Prepared:
    """The finished file, and whether it is going out as an attachment."""

    item: Item
    filename: str
    fits_upload: bool
    reason: str


def _prepare(
    url: str, tmp: Path, settings: BotSettings, upload_cap: int
) -> Prepared | None:
    """Blocking half: download, normalise, measure, aim at the upload cap.

    Runs in a worker thread. `upload_cap` is the guild's `filesize_limit`, or
    0 when uploads are off or there is no guild, in which case the link is the
    only destination and no shrink is attempted for it.

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

    shrunk_already = False
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
            shrunk_already = True
        else:
            log.info("shrink %s in %.1fs made it no smaller; keeping the original",
                     url, time.monotonic() - t1)

    if size > cap:
        log.info("skip %s - %.1fMB still over the %dMB item cap",
                 url, size / 1048576, settings.max_item_mb)
        return None

    src = _normalise(url, src, tmp, settings)
    size = src.stat().st_size
    if size > cap:
        log.info("skip %s - %.1fMB after the compatibility gate",
                 url, size / 1048576)
        return None

    _, _, duration = _dimensions(src)
    plan = upload_plan(
        size, upload_cap, duration,
        min_bitrate_kbps=settings.upload_min_bitrate_kbps,
        shrink_enabled=settings.shrink_enabled and not shrunk_already,
    )
    fits, reason = plan.action == "upload", plan.reason

    if plan.action == "shrink":
        # One attempt, aimed at the guild cap rather than the memory cap.
        fitted = tmp / "fitted.mp4"
        log.info("upload %s: %.1fMB over the %.1fMB guild cap; shrinking - %s",
                 url, size / 1048576, upload_cap / 1048576, plan.reason)
        t2 = time.monotonic()
        transcode(src, fitted, None,
                  crf=settings.shrink_crf, max_height=settings.shrink_max_height)
        fitted_size = fitted.stat().st_size if fitted.exists() else 0
        if fitted_size and fitted_size <= upload_cap:
            src, size = fitted, fitted_size
            fits = True
            reason = f"shrunk to {size / 1048576:.1f}MB in {time.monotonic() - t2:.1f}s"
        else:
            fits = False
            reason = (
                f"{fitted_size / 1048576:.1f}MB after one shrink attempt, "
                f"still over the {upload_cap / 1048576:.1f}MB guild cap"
            )
        log.info("upload %s: %s", url, reason)

    width, height, duration = _dimensions(src)
    if width is None or height is None:
        # og:video:width/height drive the player size on the link path;
        # missing them is the likeliest cause of a plain card.
        log.warning("%s has no probe dimensions; the embed may degrade", url)
    log.info("prepared %s: %.1fMB %sx%s %ss total %.1fs (attach=%s: %s)",
             url, size / 1048576, width, height, duration,
             time.monotonic() - t0, fits, reason)
    title = clean_title(info.get("title"))
    return Prepared(
        item=Item(
            data=src.read_bytes(),
            content_type="video/mp4" if src.suffix.lower() == ".mp4" else "video/webm",
            title=title,
            width=width,
            height=height,
            duration=duration,
            source_url=url,
        ),
        # Discord shows this above the player, and a raw yt-dlp title has
        # slashes, emoji and sometimes hundreds of characters.
        filename=safe_filename(title, src.suffix.lower() or ".mp4"),
        fits_upload=fits,
        reason=reason,
    )


class _Ack:
    """However we said "working on it", and how it is closed out.

    In reaction mode this is two markers, not one: a neutral hourglass while
    the work runs, swapped at the end for the single emoji that says how it
    ended. The outcome emoji stays - it is the "handled" marker, and the one a
    viewer can look up in the table in docs/bot.md.

    A message is unmissable but appears and disappears; a reaction needs Add
    Reactions on the channel and is easy to miss. Neither affects how fast
    Discord unfurls a link afterwards: a bot cannot set an embed's video
    field, so on the link path the player arrives on Discord's own schedule.
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
        if mode == "reaction" and await self._react(self._settings.emoji_working):
            self._reacted = True
            return
        if mode == "reaction":
            log.info("no Add Reactions here; acknowledging with a message instead")
        await self._post()

    async def finish(self, outcome: Outcome) -> None:
        """Swap the working marker for the one that names the outcome."""
        if self._reacted:
            await self._unreact(self._settings.emoji_working)
            if self._settings.ack_outcome_reaction:
                await self._react(self._settings.outcome_emoji(outcome))
            return
        if self._note is None:
            return
        if outcome is Outcome.FAILED:
            try:
                # Edited, not replaced: this text has no link for Discord to
                # unfurl, so there is nothing lost by editing in place.
                await self._note.edit(content=self._settings.ack_failed_text)
            except discord.HTTPException:
                log.warning("could not update the acknowledgement after a failure")
            return
        try:
            await self._note.delete()
        except discord.HTTPException:
            # Deleting its own message needs no permission, so this is just a
            # hiccup; the note is cosmetic either way.
            log.warning("could not delete the acknowledgement")

    async def _post(self) -> None:
        try:
            self._note = await self._message.reply(
                self._settings.ack_text, mention_author=False
            )
        except discord.HTTPException:
            # Not worth abandoning the job over; the video is the payload.
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


async def _attach(
    message: discord.Message, prepared: Prepared, settings: BotSettings
) -> Outcome:
    """Send the file as an attachment. UPLOADED, or why we are falling back.

    The deadline is the point of the wrapper. discord.py answers a 429 by
    sleeping `retry_after` and retrying, up to five times, and only raises
    `RateLimited` when the client was built with `max_ratelimit_timeout` and
    the wait exceeds it - never for a global limit. So without a timeout a
    throttled upload blocks instead of falling back, and the user waits for
    nothing. A cancelled upload may still have landed on Discord's side, which
    would leave both an attachment and a link; that is the right way round to
    be wrong.
    """
    file = discord.File(io.BytesIO(prepared.item.data), filename=prepared.filename)
    try:
        await asyncio.wait_for(
            message.reply(file=file, mention_author=False),
            timeout=settings.upload_timeout_seconds,
        )
    except discord.Forbidden:
        log.info("upload refused for %s: no Attach Files in this channel",
                 prepared.item.source_url)
        return Outcome.FORBIDDEN
    except discord.RateLimited as exc:
        log.info("upload throttled for %s: retry after %.1fs",
                 prepared.item.source_url, exc.retry_after)
        return Outcome.THROTTLED
    except TimeoutError:
        # asyncio.TimeoutError is TimeoutError from 3.11 on.
        log.info("upload for %s got nowhere in %.0fs; posting the link instead",
                 prepared.item.source_url, settings.upload_timeout_seconds)
        return Outcome.THROTTLED
    except discord.HTTPException as exc:
        if exc.status == 413:
            log.info("upload rejected for %s: 413, the file is over the cap",
                     prepared.item.source_url)
            return Outcome.OVERSIZE
        if exc.status == 429:
            # A 429 that reaches us unhandled is Cloudflare rather than the API.
            log.info("upload throttled for %s: 429 from the edge",
                     prepared.item.source_url)
            return Outcome.THROTTLED
        log.warning("upload failed for %s: HTTP %s %s",
                    prepared.item.source_url, exc.status, exc.text)
        return Outcome.FALLBACK
    except Exception:
        log.exception("upload failed for %s", prepared.item.source_url)
        return Outcome.FALLBACK
    return Outcome.UPLOADED


async def _warmup_target(message: discord.Message, settings: BotSettings):
    """The channel to warm against, which must be configured explicitly.

    There is no implicit target. Warming has to post the link somewhere, and
    choosing somewhere on the user's behalf means a visible side effect nobody
    asked for, so it simply does not happen until WARMUP_CHANNEL_ID names a
    channel.
    """
    channel = (
        message.guild.get_channel(settings.warmup_channel_id)
        if message.guild is not None
        else None
    )
    if channel is None:
        log.warning("warmup channel %s not found in this guild; posting unwarmed",
                    settings.warmup_channel_id)
    return channel


async def _warm(
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
    channel = await _warmup_target(message, settings)
    if channel is None:
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


def _upload_cap(message: discord.Message, settings: BotSettings) -> int:
    """The guild's attachment cap in bytes, or 0 if there is nothing to aim at.

    `filesize_limit` already accounts for boosts, so there is nothing to work
    out here beyond uploads being switched off or the message not coming from
    a guild at all.
    """
    if not settings.upload_enabled or message.guild is None:
        return 0
    return message.guild.filesize_limit


async def rehost(
    client: discord.Client,
    message: discord.Message,
    url: str,
    settings: BotSettings,
    store: MemoryStore,
    base_url: str,
) -> bool:
    """Download `url` and reply with it: attached if it fits, else as a link."""
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

        cap = _upload_cap(message, settings)
        # Keeps the typing indicator alive for the whole download, which is
        # free and needs no permission beyond posting.
        async with message.channel.typing():
            prepared = await asyncio.to_thread(_prepare, url, tmp, settings, cap)

        if prepared is None:
            await ack.finish(Outcome.FAILED)
            return False

        if prepared.fits_upload:
            outcome = await _attach(message, prepared, settings)
            if outcome is Outcome.UPLOADED:
                await ack.finish(outcome)
                log.info("uploaded %s as %s (%.1fMB, %sx%s, %.1fs end to end; "
                         "no store entry, no link)",
                         url, prepared.filename, prepared.item.size / 1048576,
                         prepared.item.width, prepared.item.height,
                         time.monotonic() - started)
                return True
            log.info("falling back to a link for %s: %s", url, outcome.value)
        else:
            outcome = Outcome.OVERSIZE
            log.info("falling back to a link for %s: %s", url, prepared.reason)

        # The link path, from here on exactly as it was before uploads: the
        # store entry, the TTL and any warmup only exist for this branch.
        store.put(key, prepared.item)
        link = f"{base_url.rstrip('/')}/d/{key}"

        if settings.warmup_channel_id:
            await _warm(message, key, link, settings, store)

        # The URL goes first and stays bare on its own line: Discord unfurls a
        # plain URL in a message that has other text, but not one wrapped in
        # <> or markdown, and the embed is the entire point.
        body = link
        if settings.link_note:
            body = f"{link}\n{settings.link_note}"
        await message.reply(body, mention_author=False)
        await ack.finish(outcome)
        log.info("posted %s as %s (%s: %.1fMB, %sx%s, held %ds, %.1fs end to end; "
                 "store now %d item(s)/%.1fMB)",
                 url, link, outcome.value, prepared.item.size / 1048576,
                 prepared.item.width, prepared.item.height,
                 settings.link_ttl_seconds, time.monotonic() - started,
                 len(store), store.total_bytes() / 1048576)
        return True

    except Exception:
        log.exception("failed to rehost %s", url)
        await ack.finish(Outcome.FAILED)
        return False
    finally:
        _inflight.discard(guard)
        shutil.rmtree(tmp, ignore_errors=True)
