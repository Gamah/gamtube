"""Whether to attach the file or post a link, and how that outcome is named.

Pure: integers, an enum, no discord.py. An attachment is strictly better than
a link - the player is there the moment the message lands, Discord becomes the
host, and there is no store entry, no TTL, no warmup and no `/d/` route in
play - so the only questions are whether it fits the guild's cap and whether
making it fit would wreck it.

The second question is the whole reason `plan` exists. The link path serves a
clip at full quality today, so squeezing a three minute video into a ten MiB
cap is a downgrade, not a win: it would need roughly 440 kbps for video and
audio together, which is unwatchable. Below `min_bitrate_kbps` the answer is
to post the link and leave the file alone.
"""

from dataclasses import dataclass
from enum import Enum


class Outcome(Enum):
    """How one link ended up. Each gets its own reaction and log line."""

    UPLOADED = "uploaded"      # native attachment, no link and no store entry
    OVERSIZE = "oversize"      # link: too big to attach, or to attach well
    FORBIDDEN = "forbidden"    # link: no Attach Files in this channel
    THROTTLED = "throttled"    # link: Discord rate limited the upload
    FALLBACK = "fallback"      # link: the upload failed for some other reason
    FAILED = "failed"          # nothing posted at all


@dataclass(frozen=True)
class Plan:
    """`action` is "upload", "shrink" or "link"; `reason` goes in the log."""

    action: str
    reason: str


def affordable_kbps(cap_bytes: int, duration_seconds: float | None) -> float | None:
    """Bitrate a clip of this length gets if it is squeezed into `cap_bytes`.

    Video plus audio, since the cap covers the whole file. None when the
    duration is unknown, which is a reason not to guess.
    """
    if cap_bytes <= 0 or not duration_seconds or duration_seconds <= 0:
        return None
    return cap_bytes * 8 / duration_seconds / 1000


def plan(
    size_bytes: int,
    cap_bytes: int,
    duration_seconds: float | None,
    *,
    min_bitrate_kbps: int,
    shrink_enabled: bool,
) -> Plan:
    """Decide what to do with a prepared file against a guild's upload cap.

    `cap_bytes` of 0 means there is no cap to aim at - uploads are off, or the
    message did not come from a guild - and then the link is the only answer.
    """
    if cap_bytes <= 0:
        return Plan("link", "no guild upload cap is known")
    if size_bytes <= cap_bytes:
        return Plan("upload", f"{size_bytes} fits the {cap_bytes} byte cap")
    if not shrink_enabled:
        return Plan("link", f"{size_bytes} is over the {cap_bytes} byte cap")

    kbps = affordable_kbps(cap_bytes, duration_seconds)
    if kbps is None:
        return Plan("link", "duration unknown, so a shrink target cannot be judged")
    if kbps < min_bitrate_kbps:
        return Plan(
            "link",
            f"fitting {duration_seconds:.0f}s into {cap_bytes} bytes allows only "
            f"{kbps:.0f}kbps, under the {min_bitrate_kbps}kbps floor",
        )
    return Plan("shrink", f"{kbps:.0f}kbps fits the {min_bitrate_kbps}kbps floor")
