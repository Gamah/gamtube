"""Telling link crawlers apart from people.

A chat app fetches `/d/{key}` to read its `og:` tags; a person who clicks the
link wants to know what they've landed on. Only the crawler needs the metadata,
so the page can serve each of them something different with nothing at risk.

**These are the agents that fetch the *page*, not the video.** Discord uses two
separate services: the unfurler reads `/d/{key}` and says `Discordbot`, while
the media proxy that fetches `/d/{key}/f` is something else whose user agent is
not documented here and must not be assumed. Reusing this list to gate the
media route would most likely block Discord's own fetcher and break every
embed. `MEDIA_USER_AGENTS` is a separate, empty-by-default list for that; see
docs/bot.md for how to fill it from your access log.

User-agent matching is a heuristic, not a security boundary: anything can claim
to be Discord. It is here to give humans a friendlier page and to keep casual
traffic off the video, not to keep anyone out.
"""

# Substrings matched case-insensitively against the full UA string. Discord is
# the one that matters; the rest are here so a link pasted elsewhere still
# unfurls. Confirm against your own access log rather than trusting this list -
# crawler UAs change without announcement.
DEFAULT_CRAWLERS: tuple[str, ...] = (
    "discordbot",
    "telegrambot",
    "slackbot",
    "twitterbot",
    "facebookexternalhit",
    "whatsapp",
    "skypeuripreview",
    "embedly",
    "redditbot",
    "signal-android",
    "signal-ios",
    "iframely",
    "vkshare",
    "mattermost",
    "revoltchat",
    "googlebot",
    "bingbot",
)


def is_crawler(user_agent: str | None, substrings=DEFAULT_CRAWLERS) -> bool:
    """True when `user_agent` looks like a link-preview crawler.

    A missing or empty UA counts as a crawler: curl and some proxies send
    nothing, and treating "unknown" as a person would mean a chat app that
    stopped identifying itself silently lost its embeds.
    """
    if not user_agent:
        return True
    ua = user_agent.lower()
    return any(s in ua for s in substrings)
