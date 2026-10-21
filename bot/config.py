from functools import lru_cache

from pydantic_settings import BaseSettings

from bot.detect import DEFAULT_DOMAINS


class BotSettings(BaseSettings):
    discord_bot_token: str = ""

    # Comma-separated guild ids the bot will act in; empty = every guild it has
    # been invited to. Channel ids in discord_channels narrow it further.
    discord_guilds: str = ""
    discord_channels: str = ""

    # Seconds to wait for Discord to attach its own embed before judging the
    # message. Too short and every link looks un-embedded; tune on a real guild.
    embed_wait_seconds: float = 3.0

    # 0 = use the guild's own filesize_limit, which already accounts for boosts.
    max_upload_mb: int = 0

    # One shrink attempt when the download lands over the cap.
    shrink_enabled: bool = True
    shrink_crf: int = 30
    shrink_max_height: int = 720

    # Domains to watch; empty = bot.detect.DEFAULT_DOMAINS.
    watch_domains: str = ""

    # Cap on uploads from one message, so a link dump can't become a flood.
    max_links_per_message: int = 3

    bot_temp_dir: str | None = None

    # "extra": "ignore" matters — the bot and the web app share one .env, and
    # pydantic-settings otherwise rejects a file holding the other's keys.
    model_config = {"env_file": ".env", "extra": "ignore"}

    def guild_ids(self) -> set[int]:
        return _int_set(self.discord_guilds)

    def channel_ids(self) -> set[int]:
        return _int_set(self.discord_channels)

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
