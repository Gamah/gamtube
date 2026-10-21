"""Where the bot may act, what it can see, and what it is missing.

The bot keeps no per-channel state of its own: it acts wherever it can see a
link, and a server excludes it by denying it View Channels there. Discord is
the whole record, so there is nothing to sync and nothing to migrate.

No discord.py import - everything here is duck-typed against
`.permissions_for`, which keeps it testable without a gateway.
"""


def allowed_by_ids(
    guild_id: int | None,
    channel_id: int,
    guilds: set[int],
    channels: set[int],
) -> bool:
    """The coarse DISCORD_GUILDS / DISCORD_CHANNELS allowlists. Empty = no limit."""
    if guilds and (guild_id is None or guild_id not in guilds):
        return False
    if channels and channel_id not in channels:
        return False
    return True


# Everything the bot needs in a channel to do its job. Any of them can be
# taken away by a channel overwrite, and the result is silence rather than an
# error, so startup reports what is missing.
REQUIRED_CAPABILITIES: tuple[str, ...] = (
    "view_channel",
    "send_messages",
    "read_message_history",
    "embed_links",
)


def missing_capabilities(
    me, channel, required: tuple[str, ...] = REQUIRED_CAPABILITIES
) -> list[str]:
    """Which of `required` the bot effectively lacks in `channel`.

    Effective permission: overwrite plus role plus category, because the
    question is "can it actually work here".
    """
    permissions_for = getattr(channel, "permissions_for", None)
    if permissions_for is None or me is None:
        return []
    perms = permissions_for(me)
    return [p for p in required if getattr(perms, p, False) is not True]


def can_view(me, channel) -> bool:
    """Whether the bot effectively sees `channel` at all.

    Effective, like `missing_capabilities`: role, category and overwrite
    together. An object that cannot answer counts as not visible.
    """
    permissions_for = getattr(channel, "permissions_for", None)
    if permissions_for is None or me is None:
        return False
    return getattr(permissions_for(me), "view_channel", False) is True


def actionable(me, channels) -> list:
    """The channels the bot will reply in, in order: the ones it can see.

    Excluding a channel means denying it View Channels there, so there is
    nothing else to consult.
    """
    return [c for c in channels if can_view(me, c)]
