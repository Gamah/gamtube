"""Title cleanup, shared by the web app and the bot.

The web app stores the cleaned title and renders it as `og:title`; the bot puts
it in the `og:title` of its own ephemeral page. Both want the same thing: the
real title, without the social-count noise some platforms prepend.
"""

import re

# Strip leading social-count noise Facebook puts before the actual title,
# e.g. "1,234 views · 567 reactions · Real Title" → "Real Title"
_SOCIAL_PREFIX = re.compile(
    r'^(?:[\d,]+(?:\.\d+)?[KMB]?\s+(?:views?|reactions?|likes?|comments?|shares?)\s*[·•|,]\s*)+',
    re.IGNORECASE,
)


def clean_title(title: str | None) -> str | None:
    if not title:
        return title
    cleaned = _SOCIAL_PREFIX.sub('', title).strip(' ·|,•')
    return cleaned if cleaned else title
