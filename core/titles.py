"""Title cleanup, shared by the web app and the bot.

The web app stores the cleaned title and renders it as `og:title`; the bot puts
it in the `og:title` of its own ephemeral page, and uses the same text as the
attachment filename Discord shows above the player - hence `safe_filename`
lives here too.
"""

import re
import unicodedata

# Strip leading social-count noise Facebook puts before the actual title,
# e.g. "1,234 views · 567 reactions · Real Title" → "Real Title"
_SOCIAL_PREFIX = re.compile(
    r'^(?:[\d,]+(?:\.\d+)?[KMB]?\s+(?:views?|reactions?|likes?|comments?|shares?)\s*[·•|,]\s*)+',
    re.IGNORECASE,
)

# Anything a filesystem or Discord would choke on, plus control characters.
_UNSAFE = re.compile(r'[\x00-\x1f\x7f/\\:*?"<>|]+')


def clean_title(title: str | None) -> str | None:
    if not title:
        return title
    cleaned = _SOCIAL_PREFIX.sub('', title).strip(' ·|,•')
    return cleaned if cleaned else title


def safe_filename(title: str | None, ext: str, max_stem: int = 80) -> str:
    """Build `<stem><ext>` safe to send as a Discord attachment name.

    `ext` is taken verbatim (expects a leading dot). A title that reduces to
    nothing - emoji-only, or all separators - falls back to "video".
    """
    stem = clean_title(title) or ""
    # NFC first so composed accents survive; strip marks that render as boxes.
    stem = unicodedata.normalize("NFC", stem)
    stem = _UNSAFE.sub(" ", stem)
    stem = re.sub(r'\s+', " ", stem).strip(" .")
    if len(stem) > max_stem:
        stem = stem[:max_stem].rstrip(" .")
    return f"{stem or 'video'}{ext}"
