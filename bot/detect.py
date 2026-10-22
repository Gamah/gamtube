"""Finding links worth re-hosting in a Discord message.

Pure functions, no discord.py - this is the cheap prefilter that decides
whether a message is worth spending a yt-dlp call on. Whether Discord actually
failed to embed the link is decided later, against the live message.
"""

import re
from collections.abc import Iterable
from urllib.parse import parse_qs, urlsplit

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

    Discord echoes back the link it unfurled, but not always byte-identical -
    a trailing slash or a tracking parameter may differ. Host plus path is the
    part that reliably survives the round trip.
    """
    parts = urlsplit(url)
    return host_of(url), parts.path.rstrip("/")


# Path segments that identify a route rather than a video, so they must never
# be used to match one link against another.
_GENERIC_SEGMENTS = frozenset({
    "watch", "video", "videos", "embed", "shorts", "live", "reel", "reels",
    "share", "status", "post", "posts", "p", "v", "clip", "clips", "story",
    "stories", "comments", "tv", "profile", "user",
})

# Query parameters that carry the video id itself.
_ID_PARAMS = ("v", "video_id", "story_fbid")


def identity(url: str) -> set[str]:
    """Keys that identify the same video across two spellings of its URL.

    Discord does not echo back the link it unfurled: it canonicalises it, so
    `youtu.be/XYZ` comes back as `youtube.com/watch?v=XYZ` and host plus path
    match nothing. Comparing on the video id as well survives that, and the
    same applies to every short-link host in the watch list - `fb.watch`,
    `redd.it`, `vm.tiktok.com`.

    Host plus path stays in the set, because a platform whose URL carries no
    usable id has nothing else to match on.

    Ids are compared case-insensitively. They are case-sensitive identifiers,
    so in principle that could match two different videos, but it needs two
    ids in one message differing only in case - against which the real risk is
    an id that arrives re-cased and matches nothing.
    """
    host, path = norm_url(url)
    keys = {f"{host}{path}".lower()}

    segment = path.rsplit("/", 1)[-1].lower()
    # Short ids exist (`redd.it/abc12`), but a four character segment is far
    # more likely to be a route than an id, and a false match means silently
    # not re-hosting something.
    if len(segment) >= 5 and segment not in _GENERIC_SEGMENTS:
        keys.add(segment)

    query = parse_qs(urlsplit(url).query)
    for param in _ID_PARAMS:
        keys.update(v.lower() for v in query.get(param, ()) if v)
    return keys


def unembedded(embeds: Iterable, candidates: list[str]) -> list[str]:
    """The candidates Discord did *not* give a playable video embed.

    Judged per link, not per message: a message holding both a YouTube link
    (which embeds fine) and a TikTok link (which doesn't) must still get the
    TikTok re-hosted. `embeds` is anything with `.url` and `.video.url`, and
    the match is on `identity`, not the URL text.
    """
    playable: set[str] = set()
    for e in embeds:
        url = getattr(e, "url", None)
        video = getattr(e, "video", None)
        if url and video is not None and getattr(video, "url", None):
            playable |= identity(url)
    return [u for u in candidates if not identity(u) & playable]
