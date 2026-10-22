"""URL shapes yt-dlp will not accept, fixed before it sees them.

Pure stdlib, so both front ends get the same treatment and it is testable
without yt-dlp installed.
"""

from urllib.parse import urlsplit, urlunsplit

# Hosts whose extractor regex only matches the `www.` form, so a bare
# registrable domain falls through to the generic extractor and raises
# UnsupportedError. Discord linkifies `tiktok.com/...` happily, and a phone's
# share sheet sometimes drops the prefix, so the bare form does arrive.
#
# Checked against yt-dlp 2026.10.21 on 2026-10-22: tiktok.com needs it;
# instagram.com, facebook.com, reddit.com, x.com and twitter.com are matched
# either way. [SOURCE] - read off the extractors' _VALID_URL, not documented.
_WWW_REQUIRED = frozenset({"tiktok.com"})


def for_extractor(url: str) -> str:
    """`url` in the form yt-dlp's extractors match, unchanged where it already is.

    Only ever adds the prefix the extractor demands: a short-link host like
    `vm.tiktok.com` or `fb.watch` is left alone, since `www.` would break it.
    """
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host not in _WWW_REQUIRED:
        return url
    port = f":{parts.port}" if parts.port else ""
    userinfo = parts.netloc.rpartition("@")[0]
    netloc = f"{userinfo}@" if userinfo else ""
    return urlunsplit(parts._replace(netloc=f"{netloc}www.{host}{port}"))
