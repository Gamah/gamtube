"""The Discord gateway client.

Started from the web app's lifespan rather than its own process, because the
videos it serves live in this process's memory (`bot.store`) and the route that
serves them is `web/routers/ephemeral.py`. `run_client` is deliberately
supervised: if the bot fails, it logs and returns, and the web app carries on.

This module imports nothing from `web/` - the dependency runs one way, so the
bot's logic stays testable without FastAPI.

Logging is deliberately chatty. Until this has run against a real guild the two
things nobody knows are how long Discord takes to unfurl each platform and what
it puts in the embed when it fails, so every decision says why it was made.
`BOT_DEBUG=true` adds per-attempt embed polling, which measures the unfurl
delay instead of assuming it.
"""

import asyncio
import logging
import time

import discord

from bot.config import BotSettings
from bot.detect import extract_candidates, unembedded
from bot.handler import rehost
from bot.scope import (
    REQUIRED_CAPABILITIES,
    allowed_by_ids,
    actionable,
    missing_capabilities,
    opted_in,
    unopted_visible,
)
from bot.store import MemoryStore

log = logging.getLogger("gamtube.bot")


def in_scope(message: discord.Message, settings: BotSettings) -> tuple[bool, str]:
    """(may act, reason). The reason is for the log - "why did nothing happen"
    is the question this bot will be asked most often."""
    guild = message.guild
    if not allowed_by_ids(
        guild.id if guild else None,
        message.channel.id,
        settings.guild_ids(),
        settings.channel_ids(),
    ):
        return False, "guild/channel not in DISCORD_GUILDS/DISCORD_CHANNELS"
    if not settings.channel_opt_in:
        return True, "opt-in disabled"
    if guild is None:
        return False, "DM, which cannot carry a permission overwrite"
    if not opted_in(guild.me, message.channel, settings.opt_in_permission):
        return False, f"channel does not explicitly grant {settings.opt_in_permission}"
    return True, "opted in"


def describe_embeds(message: discord.Message) -> str:
    """What Discord actually produced, which is the crux of the whole trigger.

    A TikTok or Instagram link usually *does* embed - as a dead link-card - so
    seeing type/url/video for each embed is what tells you whether the
    predicate is doing the right thing.
    """
    if not message.embeds:
        return "none"
    parts = []
    for e in message.embeds:
        video = getattr(e, "video", None)
        video_url = getattr(video, "url", None) if video else None
        parts.append(
            f"[type={e.type!r} url={e.url!r} video={video_url!r} "
            f"thumb={bool(getattr(e, 'thumbnail', None) and e.thumbnail.url)} "
            f"title={(e.title or '')[:40]!r}]"
        )
    return " ".join(parts)


async def _wait_for_embeds(
    message: discord.Message, settings: BotSettings
) -> discord.Message | None:
    """Re-read the message once Discord has had time to unfurl it.

    Embeds arrive asynchronously, so the message handed to `on_message` never
    has them. In debug mode this polls instead of sleeping once, which turns
    EMBED_WAIT_SECONDS from a guess into a measurement - the log says when the
    embed actually landed.
    """
    started = time.monotonic()
    wait = settings.embed_wait_seconds

    if not settings.bot_debug:
        await asyncio.sleep(wait)
        try:
            return await message.channel.fetch_message(message.id)
        except discord.NotFound:
            log.info("message %s deleted while waiting", message.id)
            return None
        except discord.HTTPException:
            log.exception("could not re-read message %s", message.id)
            return None

    interval = 0.5
    fresh = message
    while True:
        elapsed = time.monotonic() - started
        try:
            fresh = await message.channel.fetch_message(message.id)
        except discord.NotFound:
            log.info("message %s deleted while waiting", message.id)
            return None
        except discord.HTTPException:
            log.exception("could not re-read message %s", message.id)
            return None

        if fresh.embeds:
            log.debug("embeds appeared after %.2fs: %s", elapsed, describe_embeds(fresh))
            if elapsed + interval >= wait:
                return fresh
            # Keep watching: Discord sometimes upgrades a card to a player.
        else:
            log.debug("no embeds yet at %.2fs", elapsed)

        if elapsed + interval >= wait:
            if not fresh.embeds:
                log.debug("still no embeds after %.2fs", elapsed)
            return fresh
        await asyncio.sleep(interval)


def report_guild(guild: discord.Guild, settings: BotSettings) -> None:
    """Say where the bot will reply here, and where it will fall short.

    "Why did nothing happen" is the question this bot gets asked most, so the
    answer is logged once per guild at startup rather than reconstructed from
    Discord's permission UI. The capability gaps matter more than the count:
    a channel it can see but cannot post in is silent, and only this line
    says so.
    """
    channels = guild.text_channels
    active = actionable(
        guild.me, channels, settings.opt_in_permission,
        opt_in=settings.channel_opt_in,
    )
    if not active:
        log.warning(
            "%s: the bot will reply in none of its %d text channel(s) (%s)",
            guild.name, len(channels),
            f"no channel grants {settings.opt_in_permission}"
            if settings.channel_opt_in else "it cannot see any of them",
        )
        return

    log.info("%s: will reply in %d of %d text channel(s): %s",
             guild.name, len(active), len(channels), _names(active))

    required = REQUIRED_CAPABILITIES
    if settings.ack_mode == "reaction":
        # Only needed in this mode, and its absence is quiet: the bot falls
        # back to posting a message instead.
        required += ("add_reactions",)
    for channel in active:
        gaps = missing_capabilities(guild.me, channel, required)
        if gaps:
            log.warning("%s: #%s is in scope but the bot lacks %s there; "
                        "it will not be able to reply",
                        guild.name, channel.name, ", ".join(gaps))
            # No point adding that attachments won't work somewhere nothing
            # will work.
            continue
        if missing_capabilities(guild.me, channel, ("attach_files",)):
            # Not a gap in the required set: without it the upload is refused
            # and the link goes out instead, which still works. Worth saying
            # once rather than per clip.
            log.info("%s: #%s has no Attach Files for the bot, "
                     "so every clip there goes out as a link",
                     guild.name, channel.name)

    if settings.channel_opt_in:
        # Only meaningful under opt-in: the gap between what it sees and what
        # it may act in is what the category deny procedure closes.
        unopted = unopted_visible(guild.me, channels, settings.opt_in_permission)
        if unopted:
            log.warning(
                "%s: %d of %d text channel(s) visible but not opted in (%s); "
                "deny View Channels to the bot's role at the category level to "
                "make it blind - see docs/bot.md",
                guild.name, len(unopted), len(channels), _names(unopted),
            )
        else:
            log.info("%s: blind outside its opted-in channels (0 of %d text "
                     "channel(s) visible but not opted in)",
                     guild.name, len(channels))


def _names(channels: list, limit: int = 5) -> str:
    names = [c.name for c in channels[:limit]]
    if len(channels) > len(names):
        names.append(f"and {len(channels) - len(names)} more")
    return ", ".join(names)


def build_client(
    settings: BotSettings, store: MemoryStore, base_url: str
) -> discord.Client:
    intents = discord.Intents.default()
    # Privileged. Enable it on the application's Bot page or every message
    # arrives with empty content and the bot sees no links at all.
    intents.message_content = True

    client = discord.Client(intents=intents)

    @client.event
    async def on_ready() -> None:
        log.info("connected as %s in %d guild(s)", client.user, len(client.guilds))
        log.info(
            "config: wait=%.1fs opt_in=%s/%s item<=%dMB store<=%dMB ttl=%ds "
            "force_mp4=%s notice=%s media_gate=%s warm=%s",
            settings.embed_wait_seconds, settings.channel_opt_in,
            settings.opt_in_permission, settings.max_item_mb,
            settings.max_memory_mb, settings.link_ttl_seconds,
            settings.force_mp4, settings.human_notice,
            settings.media_agents() or "open",
            settings.warmup_channel_id or "off",
        )
        for guild in client.guilds:
            report_guild(guild, settings)

    @client.event
    async def on_message(message: discord.Message) -> None:
        if message.author.bot:
            return

        candidates = extract_candidates(message.content, settings.domains())
        allowed, reason = in_scope(message, settings)

        if not allowed:
            # Only interesting when there was something to act on.
            if candidates:
                log.info("ignoring %d link(s) in #%s: %s",
                         len(candidates), getattr(message.channel, "name", "?"), reason)
            return
        if not candidates:
            log.debug("no candidate links in message %s", message.id)
            return

        log.info("message %s in #%s has %d candidate(s): %s",
                 message.id, getattr(message.channel, "name", "?"),
                 len(candidates), ", ".join(candidates))

        fresh = await _wait_for_embeds(message, settings)
        if fresh is None:
            return

        log.info("after %.1fs discord produced: %s",
                 settings.embed_wait_seconds, describe_embeds(fresh))

        todo = unembedded(fresh.embeds, candidates)
        if not todo:
            log.info("all %d link(s) already embedded playably; nothing to do",
                     len(candidates))
            return

        capped = todo[: settings.max_links_per_message]
        if len(capped) < len(todo):
            log.info("%d link(s) need rehosting, handling %d (MAX_LINKS_PER_MESSAGE)",
                     len(todo), len(capped))
        else:
            log.info("%d link(s) need rehosting", len(capped))

        for url in capped:
            await rehost(client, fresh, url, settings, store, base_url)

    return client


async def run_client(
    settings: BotSettings, store: MemoryStore, base_url: str
) -> None:
    """Run the gateway client until cancelled, swallowing its failures.

    Anything other than cancellation is logged and returns, so a bad token or a
    gateway problem degrades to "no bot" rather than taking the web app down.
    """
    if settings.opt_in_permission not in discord.Permissions.VALID_FLAGS:
        log.error(
            "OPT_IN_PERMISSION=%r is not a Discord permission name; bot not started",
            settings.opt_in_permission,
        )
        return
    try:
        client = build_client(settings, store, base_url)
        async with client:
            await client.start(settings.discord_bot_token)
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("Discord client stopped; the web app keeps running")
