"""gamtube Discord bot — python -m bot

Watches for links from platforms Discord fails to embed playably, downloads
them with yt-dlp and replies with the video as an attachment. Discord's CDN
then holds the file; this process keeps nothing.

Channels opt in through Discord's own permissions — see bot/scope.py.
"""

import asyncio
import logging
import sys

import discord

from bot.config import BotSettings, get_bot_settings
from bot.detect import extract_candidates, unembedded
from bot.handler import rehost
from bot.scope import allowed_by_ids, opted_in

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


def build_client(settings: BotSettings) -> discord.Client:
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
            await rehost(fresh, url, settings)

    return client


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-5.5s [%(name)s] %(message)s",
    )
    settings = get_bot_settings()
    if not settings.discord_bot_token:
        print("DISCORD_BOT_TOKEN is not set; nothing to do.", file=sys.stderr)
        return 1
    if settings.opt_in_permission not in discord.Permissions.VALID_FLAGS:
        print(
            f"OPT_IN_PERMISSION={settings.opt_in_permission!r} is not a Discord "
            "permission name.",
            file=sys.stderr,
        )
        return 1
    build_client(settings).run(settings.discord_bot_token, log_handler=None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
