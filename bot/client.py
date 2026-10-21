"""The Discord gateway client.

Started from the web app's lifespan rather than its own process, because the
videos it serves live in this process's memory (`bot.store`) and the route that
serves them is `web/routers/ephemeral.py`. `run_client` is deliberately
supervised: if the bot fails, it logs and returns, and the web app carries on.

This module imports nothing from `web/` — the dependency runs one way, so the
bot's logic stays testable without FastAPI.
"""

import asyncio
import logging

import discord

from bot.config import BotSettings
from bot.detect import extract_candidates, unembedded
from bot.handler import rehost
from bot.scope import allowed_by_ids, opted_in
from bot.store import MemoryStore

log = logging.getLogger("gamtube.bot")


def in_scope(message: discord.Message, settings: BotSettings) -> bool:
    guild = message.guild
    if not allowed_by_ids(
        guild.id if guild else None,
        message.channel.id,
        settings.guild_ids(),
        settings.channel_ids(),
    ):
        return False
    if not settings.channel_opt_in:
        return True
    if guild is None:
        return False  # a DM carries no overwrites, so it can never opt in
    return opted_in(guild.me, message.channel, settings.opt_in_permission)


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
        if not settings.channel_opt_in:
            log.info("channel opt-in disabled; acting wherever the allowlists permit")
            return
        # Worth logging loudly: an opt-in bot with nothing granted looks broken,
        # and this is the line that explains why it is sitting there silently.
        for guild in client.guilds:
            enabled = [
                c.name for c in guild.text_channels
                if opted_in(guild.me, c, settings.opt_in_permission)
            ]
            if enabled:
                log.info("%s: enabled in %s", guild.name, ", ".join(enabled))
            else:
                log.warning(
                    "%s: no channel grants %s to the bot, so it will do nothing here",
                    guild.name, settings.opt_in_permission,
                )

    @client.event
    async def on_message(message: discord.Message) -> None:
        if message.author.bot or not in_scope(message, settings):
            return

        candidates = extract_candidates(message.content, settings.domains())
        if not candidates:
            return

        # Discord unfurls after the message arrives, so the copy delivered here
        # never has embeds yet. Wait, then re-read the message to see what it
        # actually managed to embed.
        await asyncio.sleep(settings.embed_wait_seconds)
        try:
            fresh = await message.channel.fetch_message(message.id)
        except discord.NotFound:
            return  # deleted while we waited
        except discord.HTTPException:
            log.exception("could not re-read message %s", message.id)
            return

        todo = unembedded(fresh.embeds, candidates)[: settings.max_links_per_message]
        for url in todo:
            await rehost(fresh, url, settings, store, base_url)

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
