from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings

from bot.agents import DEFAULT_CRAWLERS
from bot.detect import DEFAULT_DOMAINS
from bot.upload import Outcome


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

    # Memory budgets for the link path, not Discord's upload cap: a clip over
    # max_item_mb is skipped outright, and max_memory_mb caps the whole store.
    max_item_mb: int = 100
    max_memory_mb: int = 512

    # Attach the file when it fits the guild's filesize_limit, which already
    # accounts for boosts. An attachment needs no store entry, no TTL, no
    # warmup and no /d/ route, so it is the shorter path as well as the better
    # one. Anything that does not fit, and any refusal, falls back to the link.
    upload_enabled: bool = True

    # Seconds one upload attempt gets before the link goes out instead.
    # discord.py answers a 429 by sleeping and retrying, up to five times, so
    # without a deadline a throttled upload blocks rather than falling back.
    upload_timeout_seconds: float = 45.0

    # Floor for a shrink aimed at the guild cap, video and audio together.
    # Below it, fitting the clip costs more than the link path does: the link
    # serves it untouched, so a ruined attachment is a downgrade. 900kbps is
    # roughly 93s of clip inside a non-boosted 10MiB cap.
    upload_min_bitrate_kbps: int = 900

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

    # Download plus any re-encode can take the better part of a minute, which
    # looks like nothing happening. Acknowledge straight away, then replace the
    # note with the link. Posting the link as a NEW message rather than editing
    # the note is deliberate: a fresh message is reliably unfurled.
    #   reaction - react to the original (default); nothing appears and
    #              vanishes. Needs Add Reactions on the channel, and falls back
    #              to message without it, which on_ready warns about.
    #   message  - reply "working on it...", then post the link and delete it
    #   none     - stay silent until the link is ready
    ack_mode: Literal["message", "reaction", "none"] = "reaction"
    # Appended under the link. Viewers see a URL that plays inline but goes
    # nowhere useful if clicked, so say so. Empty for no note.
    link_note: str = "(clicking this link does nothing)"

    ack_text: str = "working on it\u2026"
    ack_failed_text: str = "could not fetch that one."

    # The reaction is two separate markers. emoji_working goes on while the
    # work runs and is deliberately neutral; when the work ends it is swapped
    # for the one emoji that says how it ended, which stays put as the
    # "handled" marker and is what docs/bot.md tabulates. That replaces the
    # old ack_keep_reaction, whose check mark now means "uploaded natively"
    # specifically. Set ack_outcome_reaction=false to just clear the working
    # marker and leave no trail.
    ack_outcome_reaction: bool = True
    emoji_working: str = "\u23f3"          # hourglass, in progress
    emoji_uploaded: str = "\u2705"         # check mark: attached natively
    emoji_oversize: str = "\U0001f4e6"     # package: too big to attach
    emoji_forbidden: str = "\U0001f512"    # lock: no Attach Files here
    emoji_throttled: str = "\U0001f40c"    # snail: Discord rate limited us
    emoji_fallback: str = "\U0001f914"     # thinking face: generic fallback
    emoji_failed: str = "\u274c"           # cross mark: nothing was posted

    # Posting the link is what makes Discord fetch it, so the player always
    # lands after the message. Warming works around that: post the link in a
    # throwaway channel first, wait until we have served the whole file (plus a
    # settling pause), delete it, then post publicly against Discord's now-warm
    # per-URL unfurl cache.
    #
    # 0 = off, and there is deliberately no implicit target. Warming has to
    # post the link somewhere, and choosing somewhere on the user's behalf
    # means a visible side effect nobody asked for. Name a channel in the same
    # guild that the bot can post in.
    warmup_channel_id: int = 0
    warmup_timeout_seconds: float = 20.0
    warmup_settle_seconds: float = 3.0

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
    # for /d/{key}/f - guessing it blocks Discord and kills every embed.
    media_user_agents: str = ""

    # Verbose instrumentation while the bot is being tuned: full request
    # headers on /d/, per-stage timings, and polling the message so the actual
    # embed-arrival delay is measured rather than guessed. Costs extra Discord
    # API calls, so it is off by default.
    bot_debug: bool = False

    bot_temp_dir: str | None = None

    # "extra": "ignore" matters - the bot and the web app share one .env, and
    # pydantic-settings otherwise rejects a file holding the other's keys.
    model_config = {"env_file": ".env", "extra": "ignore"}

    def outcome_emoji(self, outcome: Outcome) -> str:
        return {
            Outcome.UPLOADED: self.emoji_uploaded,
            Outcome.OVERSIZE: self.emoji_oversize,
            Outcome.FORBIDDEN: self.emoji_forbidden,
            Outcome.THROTTLED: self.emoji_throttled,
            Outcome.FALLBACK: self.emoji_fallback,
            Outcome.FAILED: self.emoji_failed,
        }[outcome]

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
