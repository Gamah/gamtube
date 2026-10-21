from functools import lru_cache

from pydantic_settings import BaseSettings

from bot.agents import DEFAULT_CRAWLERS
from bot.detect import DEFAULT_DOMAINS


class BotSettings(BaseSettings):
    discord_bot_token: str = ""

    # Opt-in is a Discord permission overwrite, not bot state: a channel counts
    # as enabled when it explicitly grants the bot opt_in_permission. Set
    # channel_opt_in=false to act everywhere the allowlists below permit.
    channel_opt_in: bool = True
    opt_in_permission: str = "embed_links"

    # Coarse allowlists applied before the opt-in check; empty = no limit.
    discord_guilds: str = ""
    discord_channels: str = ""

    # Seconds to wait for Discord to attach its own embed before judging the
    # message. Too short and every link looks un-embedded; tune on a real guild.
    embed_wait_seconds: float = 3.0

    # The link is served from RAM, so these are memory budgets, not Discord's
    # upload cap — nothing is attached. A clip over max_item_mb is skipped.
    max_item_mb: int = 100
    max_memory_mb: int = 512

    # Seconds a posted link keeps working. Short on purpose: Discord only needs
    # it long enough to unfurl. See bot/store.py for what this bets on.
    link_ttl_seconds: int = 300

    # Discord's embed player wants H.264 mp4; the format selector asks for it
    # first, and this re-encodes anything that still arrives as something else.
    force_mp4: bool = True

    # One shrink attempt when the download lands over max_item_mb.
    shrink_enabled: bool = True
    shrink_crf: int = 30
    shrink_max_height: int = 720

    # Domains to watch; empty = bot.detect.DEFAULT_DOMAINS.
    watch_domains: str = ""

    # Cap on uploads from one message, so a link dump can't become a flood.
    max_links_per_message: int = 3

    # Who counts as a link crawler; empty = bot.agents.DEFAULT_CRAWLERS.
    crawler_user_agents: str = ""

    # Show a "this is for Discord" page to people who open /d/ in a browser.
    # Only crawlers need the og: tags, so this costs nothing.
    human_notice: bool = True

    # UA substrings allowed to fetch the video bytes; empty (default) serves
    # anyone. Deliberately NOT crawler_user_agents: the page is fetched by
    # Discord's unfurler ("Discordbot"), the video by its media proxy, which is
    # a different service. Populate this from what actually appears in the log
    # for /d/{key}/f — guessing it blocks Discord and kills every embed.
    media_user_agents: str = ""

    bot_temp_dir: str | None = None

    # "extra": "ignore" matters — the bot and the web app share one .env, and
    # pydantic-settings otherwise rejects a file holding the other's keys.
    model_config = {"env_file": ".env", "extra": "ignore"}

    def guild_ids(self) -> set[int]:
        return _int_set(self.discord_guilds)

    def channel_ids(self) -> set[int]:
        return _int_set(self.discord_channels)

    def crawlers(self) -> tuple[str, ...]:
        names = tuple(
            u.strip().lower() for u in self.crawler_user_agents.split(",") if u.strip()
        )
        return names or DEFAULT_CRAWLERS

    def media_agents(self) -> tuple[str, ...]:
        return tuple(
            u.strip().lower() for u in self.media_user_agents.split(",") if u.strip()
        )

    def domains(self) -> frozenset[str]:
        names = {d.strip().lower().removeprefix("www.") for d in self.watch_domains.split(",")}
        names.discard("")
        return frozenset(names) if names else DEFAULT_DOMAINS


def _int_set(raw: str) -> set[int]:
    out: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            out.add(int(part))
    return out


@lru_cache(maxsize=1)
def get_bot_settings() -> BotSettings:
    return BotSettings()
