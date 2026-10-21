"""HTTP Range header parsing.

Discord's media proxy fetches video with byte-range requests, so the ephemeral
endpoint has to answer `206 Partial Content` properly or the embed player
stalls. Pure and dependency-free so it can be tested directly.
"""


def parse_range(header: str | None, size: int) -> tuple[int, int] | None:
    """Resolve a Range header to an inclusive `(start, end)` byte pair.

    Returns `None` when the whole body should be sent — no header, a unit we
    don't support, or anything malformed, all of which RFC 9110 says to ignore
    rather than reject. Raises `ValueError` when the range is syntactically
    fine but cannot be satisfied, which is a `416`.

    Only a single range is honoured; a multi-range request falls back to the
    whole body rather than building a multipart response.
    """
    if not header or size <= 0:
        return None

    unit, _, spec = header.partition("=")
    if unit.strip().lower() != "bytes" or not spec.strip():
        return None
    if "," in spec:
        return None

    start_raw, sep, end_raw = spec.strip().partition("-")
    if not sep:
        return None

    try:
        if not start_raw:
            # "bytes=-500" — the final 500 bytes.
            length = int(end_raw)
            if length <= 0:
                raise ValueError("unsatisfiable suffix range")
            return max(0, size - length), size - 1

        start = int(start_raw)
        end = int(end_raw) if end_raw else size - 1
    except ValueError as exc:
        if "unsatisfiable" in str(exc):
            raise
        return None

    if start < 0:
        return None
    # Bounds before inversion: an open-ended "bytes=9999-" defaults end to
    # size-1, which looks inverted, and must still be a 416 rather than a
    # silent fall back to the whole body.
    if start >= size:
        raise ValueError("unsatisfiable range")
    if end < start:
        return None

    return start, min(end, size - 1)
