"""What has to be true of an mp4 before Discord will play it, pure stdlib.

Two independent things decide whether a downloaded file plays everywhere:

* the **box layout**. ISO/IEC 14496-12 4.2 gives every box a header of a 32 bit
  size followed by a 4 character type, so the top level can be walked by
  arithmetic alone, with no decoder and no ffprobe. What matters is whether
  `moov` precedes `mdat`: a player fetching over ranged HTTP, which is how
  `/d/{key}/f` is read, cannot start until it has the index, and a `moov` at
  the end of the file is the usual cause of an embed whose audio runs while
  the picture never advances.
* the **codec facts**, which come from ffprobe output. That output is a plain
  dict, so judging it stays pure too and the whole gate is testable here.

Nothing in this module runs a subprocess or opens a file. The caller probes and
maps the file in; `bot/handler.py` does both.
"""

from dataclasses import dataclass

# Profiles browsers and Discord's player actually decode. high10, 4:2:2 and
# 4:4:4 are valid H.264 and play in VLC, which is how they reach us unnoticed.
PLAYABLE_PROFILES = frozenset({
    "baseline", "constrained baseline", "main", "high",
})

# yuvj420p is the same 4:2:0 layout with full range flagged; players handle it.
PLAYABLE_PIX_FMTS = frozenset({"yuv420p", "yuvj420p"})


@dataclass(frozen=True)
class Verdict:
    """`action` is "ok", "remux" or "reencode"; `reason` goes in the log."""

    action: str
    reason: str


def top_level_boxes(data) -> list[tuple[str, int, int]]:
    """Walk the top level boxes of `data`, returning (type, offset, size).

    `data` is anything sliceable: bytes, a memoryview, or an mmap of the file,
    which is how a multi megabyte mdat gets skipped without being read.

    Per ISO/IEC 14496-12 4.2 a size of 1 means the real size is a 64 bit
    largesize after the type, and a size of 0 means the box runs to the end of
    the file. A `uuid` box carries a 16 byte extended type after the header,
    but since the walk advances by the box size it needs no special case and
    reports its type as "uuid". A box that claims to run past the end of the
    data, or to be shorter than its own header, ends the walk.
    """
    total = len(data)
    boxes: list[tuple[str, int, int]] = []
    offset = 0
    while offset + 8 <= total:
        size = int.from_bytes(data[offset:offset + 4], "big")
        btype = bytes(data[offset + 4:offset + 8]).decode("latin-1")
        header = 8
        if size == 1:
            if offset + 16 > total:
                break
            size = int.from_bytes(data[offset + 8:offset + 16], "big")
            header = 16
        elif size == 0:
            size = total - offset
        if size < header or offset + size > total:
            break
        boxes.append((btype, offset, size))
        offset += size
    return boxes


def moov_before_mdat(boxes: list[tuple[str, int, int]]) -> bool | None:
    """True when the index precedes the media. None if either box is absent."""
    moov = next((off for t, off, _ in boxes if t == "moov"), None)
    mdat = next((off for t, off, _ in boxes if t == "mdat"), None)
    if moov is None or mdat is None:
        return None
    return moov < mdat


def _streams(probe: dict, kind: str) -> list[dict]:
    return [s for s in probe.get("streams", []) if s.get("codec_type") == kind]


def compat_verdict(probe: dict, boxes: list[tuple[str, int, int]]) -> Verdict:
    """Decide whether an mp4 is served as-is, remuxed, or re-encoded.

    A remux is only ever enough for a layout problem: the streams are already
    playable and `-c copy -movflags +faststart` rewrites the index. Anything
    about the streams themselves needs a real re-encode.
    """
    video = _streams(probe, "video")
    if not video:
        return Verdict("reencode", "no video stream was probed")
    v = video[0]

    codec = (v.get("codec_name") or "").lower()
    if codec != "h264":
        return Verdict("reencode", f"video codec is {codec or 'unknown'}, not h264")

    pix_fmt = (v.get("pix_fmt") or "").lower()
    if pix_fmt not in PLAYABLE_PIX_FMTS:
        return Verdict("reencode", f"pix_fmt is {pix_fmt or 'unknown'}, not yuv420p")

    profile = (v.get("profile") or "").lower()
    if profile not in PLAYABLE_PROFILES:
        return Verdict("reencode", f"H.264 profile is {profile or 'unknown'}")

    width, height = v.get("width"), v.get("height")
    if not width or not height:
        return Verdict("reencode", "no probe dimensions")
    if width % 2 or height % 2:
        return Verdict("reencode", f"odd dimensions {width}x{height}")

    audio = _streams(probe, "audio")
    if audio:
        acodec = (audio[0].get("codec_name") or "").lower()
        if acodec != "aac":
            return Verdict("reencode", f"audio codec is {acodec or 'unknown'}, not aac")

    faststart = moov_before_mdat(boxes)
    if faststart is None:
        return Verdict("remux", "could not find both moov and mdat")
    if not faststart:
        return Verdict("remux", "moov follows mdat, so ranged playback stalls")

    return Verdict("ok", "h264/aac yuv420p with moov first")
