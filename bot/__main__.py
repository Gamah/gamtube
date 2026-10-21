"""gamtube Discord bot — python -m bot

Watches for links from platforms Discord fails to embed playably, downloads
them with yt-dlp and replies with the video as an attachment. Discord's CDN
then holds the file; this process keeps nothing.
"""

import asyncio
import logging
import sys

import discord

from bot.config import BotSettings, get_bot_settings
from bot.detect import extract_candidates, unembedded
from bot.handler import rehost

log = logging.getLogger("gamtube.bot")


def in_scope(message: discord.Message, settings: BotSettings) -> bool:
    guilds = settings.guild_ids()
    channels = settings.channel_ids()
    if guilds and (message.guild is None or message.guild.id not in guilds):
        return False
    if channels and message.channel.id not in channels:
        return False
    return True


def build_client(settings: BotSettings) -> discord.Client:
    intents = discord.Intents.default()
    # Privileged. Enable it on the application's Bot page or every message
    # arrives with empty content and the bot sees no links at all.
    intents.message_content = True

    client = discord.Client(intents=intents)

    @client.event
    async def on_ready() -> None:
        log.info("connected as %s in %d guild(s)", client.user, len(client.guilds))

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
    build_client(settings).run(settings.discord_bot_token, log_handler=None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
