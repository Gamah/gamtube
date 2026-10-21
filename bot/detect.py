"""Finding links worth re-hosting in a Discord message.

Pure functions, no discord.py — this is the cheap prefilter that decides
whether a message is worth spending a yt-dlp call on. Whether Discord actually
failed to embed the link is decided later, against the live message.
"""

import re
from collections.abc import Iterable
from urllib.parse import urlsplit

# Platforms whose links Discord routinely fails to give a playable embed.
# Registrable domain only; subdomains are matched by suffix.
DEFAULT_DOMAINS: frozenset[str] = frozenset({
    "tiktok.com",
    "instagram.com",
    "facebook.com",
    "fb.watch",
    "fb.com",
    "reddit.com",
    "redd.it",
    "x.com",
    "twitter.com",
    "youtube.com",
    "youtu.be",
    "threads.net",
    "snapchat.com",
    "tumblr.com",
    "twitch.tv",
    "vimeo.com",
    "bsky.app",
})

# A bare URL in message text. The negative lookbehind skips `<https://...>`:
# that syntax is how a Discord user deliberately suppresses an embed, so
# honour it rather than overriding them.
_URL = re.compile(r'(?<![<\w])https?://[^\s<>()\[\]{}"\'`]+', re.IGNORECASE)

# Sentence punctuation that commonly trails a pasted link.
_TRAILING = '.,!?;:\'"*_~'


def _trim(url: str) -> str:
    url = url.rstrip(_TRAILING)
    # Markdown link syntax and wrapping parens leave an unbalanced closer.
    while url and url[-1] in ')]' and url.count(url[-1]) > url.count({')': '(', ']': '['}[url[-1]]):
        url = url[:-1].rstrip(_TRAILING)
    return url


def host_of(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def is_candidate(url: str, domains: frozenset[str] = DEFAULT_DOMAINS) -> bool:
    host = host_of(url)
    return any(host == d or host.endswith("." + d) for d in domains)


def extract_candidates(
    content: str, domains: frozenset[str] = DEFAULT_DOMAINS
) -> list[str]:
    """Candidate URLs in `content`, in order, deduplicated."""
    seen: set[str] = set()
    out: list[str] = []
    for raw in _URL.findall(content or ""):
        url = _trim(raw)
        if url and url not in seen and is_candidate(url, domains):
            seen.add(url)
            out.append(url)
    return out


def norm_url(url: str) -> tuple[str, str]:
    """Host and path, for comparing our candidates against Discord's embeds.

    Discord echoes back the link it unfurled, but not always byte-identical —
    a trailing slash or a tracking parameter may differ. Host plus path is the
    part that reliably survives the round trip.
    """
    parts = urlsplit(url)
    return host_of(url), parts.path.rstrip("/")


def unembedded(embeds: Iterable, candidates: list[str]) -> list[str]:
    """The candidates Discord did *not* give a playable video embed.

    Judged per link, not per message: a message holding both a YouTube link
    (which embeds fine) and a TikTok link (which doesn't) must still get the
    TikTok re-hosted. `embeds` is anything with `.url` and `.video.url`.
    """
    playable = set()
    for e in embeds:
        url = getattr(e, "url", None)
        video = getattr(e, "video", None)
        if url and video is not None and getattr(video, "url", None):
            playable.add(norm_url(url))
    return [u for u in candidates if norm_url(u) not in playable]
