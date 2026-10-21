"""Deciding whether the bot may act in a channel.

Opt-in is Discord's own permission system, not state the bot keeps: a channel
is opted in when it carries an **explicit** permission overwrite granting the
bot `OPT_IN_PERMISSION`. Adding the bot to a channel is then a normal channel
permission edit, it survives restarts because Discord stores it, and there is
no file, database or command to keep in sync.

No discord.py import - everything here is duck-typed against `.overwrites_for`,
`.roles` and `.parent`, which keeps it testable without a gateway.
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


def permission_target(channel):
    """The object whose overwrites govern `channel`.

    Threads have no overwrites of their own - discord.py doesn't even give
    `Thread` an `overwrites_for` - so a thread is governed by its parent.
    """
    return getattr(channel, "parent", None) or channel


def opted_in(me, channel, permission: str) -> bool:
    """True when `permission` is explicitly granted to the bot on this channel.

    Only an explicit overwrite counts. An inherited or server-wide grant does
    not, because the point is that enabling a channel is a deliberate act.
    The `@everyone` overwrite is skipped for the same reason: `attach_files` is
    commonly granted to `@everyone`, and honouring that would opt in every
    channel on the server at once.

    Grants land on either the bot's member object or its managed role
    depending on how the admin added it, so both are checked.
    """
    if me is None:
        return False

    target = permission_target(channel)
    overwrites_for = getattr(target, "overwrites_for", None)
    if overwrites_for is None:
        return False

    if getattr(overwrites_for(me), permission, None) is True:
        return True

    default_role = getattr(getattr(me, "guild", None), "default_role", None)
    for role in getattr(me, "roles", ()):
        if default_role is not None and role == default_role:
            continue
        if getattr(overwrites_for(role), permission, None) is True:
            return True

    return False


# Everything the bot needs in a channel to do its job. With nothing granted at
# the role level these are all per-channel, so a half-ticked channel is an easy
# mistake - and a silent one, since the bot simply never replies.
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

    Unlike `opted_in`, this is the *effective* permission - overwrite plus role
    plus category - because the question here is "can it actually work", not
    "was it deliberately switched on".
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


def actionable(me, channels, permission: str, *, opt_in: bool) -> list:
    """The channels the bot will actually reply in, in order.

    With `opt_in` off - the default - that is every channel it can see, and
    declining to act somewhere is an admin denying it View Channels there.
    With `opt_in` on it is the far smaller set carrying an explicit grant of
    `permission`. Both are "can see" first: an invisible channel is not a
    candidate under either rule.
    """
    return [
        c for c in channels
        if can_view(me, c) and (not opt_in or opted_in(me, c, permission))
    ]


def unopted_visible(me, channels, permission: str) -> list:
    """The channels the bot can see but was never opted into.

    This is the number that should be zero. Install permissions of nothing
    still leave a bot inheriting `@everyone`, so it usually sees the whole
    server and merely declines to act; blindness is View Channels *denied* to
    its role at the category level and allowed per opted-in channel. The
    procedure is in `docs/bot.md`, and this is what says whether it took.

    Order is preserved, so the caller can name the first few.
    """
    return [
        c for c in channels
        if can_view(me, c) and not opted_in(me, c, permission)
    ]
