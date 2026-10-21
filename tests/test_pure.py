"""The pure parts of the bot: link detection and filename/title handling.

No token, no network, no venv — these are the only pieces of the bot that can
be exercised without a live gateway, which is why they live outside the
discord.py handlers. Run: python3 tests/test_pure.py
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from bot.detect import extract_candidates, is_candidate, norm_url, unembedded
from core.titles import clean_title, safe_filename

fails = []


def eq(label, got, want):
    if got != want:
        fails.append(f"{label}\n   got:  {got!r}\n   want: {want!r}")


# ---- extract_candidates ----
eq("bare tiktok",
   extract_candidates("look at this https://www.tiktok.com/@u/video/123"),
   ["https://www.tiktok.com/@u/video/123"])

eq("suppressed <> is honoured",
   extract_candidates("<https://www.tiktok.com/@u/video/123>"),
   [])

eq("markdown link, trailing paren trimmed",
   extract_candidates("[clip](https://vm.tiktok.com/abc123)"),
   ["https://vm.tiktok.com/abc123"])

eq("trailing sentence punctuation",
   extract_candidates("see https://www.instagram.com/reel/XYZ/ ."),
   ["https://www.instagram.com/reel/XYZ/"])

eq("trailing period attached",
   extract_candidates("see https://www.instagram.com/reel/XYZ."),
   ["https://www.instagram.com/reel/XYZ"])

eq("wrapped in parens",
   extract_candidates("(https://fb.watch/abc/)"),
   ["https://fb.watch/abc/"])

eq("dedup repeats",
   extract_candidates("https://x.com/a/1 and again https://x.com/a/1"),
   ["https://x.com/a/1"])

eq("ignores unknown domain",
   extract_candidates("https://example.com/video.mp4"),
   [])

eq("multiple, order preserved",
   extract_candidates("https://x.com/a/1 then https://www.tiktok.com/@u/video/9"),
   ["https://x.com/a/1", "https://www.tiktok.com/@u/video/9"])

eq("no urls", extract_candidates("just talking"), [])
eq("empty content", extract_candidates(""), [])
eq("none content", extract_candidates(None), [])

# ---- is_candidate ----
eq("subdomain matches", is_candidate("https://vm.tiktok.com/x"), True)
eq("www stripped", is_candidate("https://www.tiktok.com/x"), True)
eq("lookalike domain rejected", is_candidate("https://eviltiktok.com/x"), False)
eq("suffix-only rejected", is_candidate("https://nottiktok.com/x"), False)
eq("uppercase host", is_candidate("https://WWW.TikTok.COM/x"), True)
eq("custom domain set",
   is_candidate("https://example.com/v", frozenset({"example.com"})), True)

# ---- norm_url / unembedded ----
class E:
    def __init__(self, url, video=True):
        self.url = url
        self.video = type("V", (), {"url": "https://cdn/x.mp4"})() if video else None

eq("trailing slash equal",
   norm_url("https://x.com/a/1/"), norm_url("https://x.com/a/1"))
eq("query ignored",
   norm_url("https://x.com/a/1?utm=1"), norm_url("https://x.com/a/1"))

eq("playable embed removes its own link",
   unembedded([E("https://x.com/a/1")], ["https://x.com/a/1"]),
   [])

eq("mixed message keeps the unembedded one",
   unembedded([E("https://www.youtube.com/watch?v=1")],
              ["https://www.youtube.com/watch?v=1", "https://www.tiktok.com/@u/video/9"]),
   ["https://www.tiktok.com/@u/video/9"])

eq("embed without video does not count",
   unembedded([E("https://www.tiktok.com/@u/video/9", video=False)],
              ["https://www.tiktok.com/@u/video/9"]),
   ["https://www.tiktok.com/@u/video/9"])

eq("no embeds at all",
   unembedded([], ["https://x.com/a/1"]), ["https://x.com/a/1"])

# ---- clean_title ----
eq("facebook noise", clean_title("1.2K views · 34 reactions · Actual Title"), "Actual Title")
eq("multiple counts", clean_title("5 views · 2 likes · 9 shares · Real"), "Real")
eq("no noise untouched", clean_title("Just A Title"), "Just A Title")
eq("noise only falls back", clean_title("1.2K views · "), "1.2K views · ")
eq("none passthrough", clean_title(None), None)
eq("empty passthrough", clean_title(""), "")

# ---- safe_filename ----
eq("slashes removed", safe_filename("a/b\\c", ".mp4"), "a b c.mp4")
eq("extension kept", safe_filename("clip", ".webm"), "clip.webm")
eq("emoji-only falls back", safe_filename("🎉🎉", ".mp4"), "🎉🎉.mp4")
eq("empty falls back", safe_filename("", ".mp4"), "video.mp4")
eq("none falls back", safe_filename(None, ".mp4"), "video.mp4")
eq("separators-only falls back", safe_filename("///", ".mp4"), "video.mp4")
eq("control chars stripped", safe_filename("a\x00\x1fb", ".mp4"), "a b.mp4")
eq("whitespace collapsed", safe_filename("a    b\n\tc", ".mp4"), "a b c.mp4")
eq("trailing dot stripped", safe_filename("title...", ".mp4"), "title.mp4")
eq("facebook noise cleaned too",
   safe_filename("1.2K views · 34 reactions · Real Title", ".mp4"), "Real Title.mp4")
eq("length capped", len(safe_filename("x" * 300, ".mp4")), 84)
eq("cap does not leave trailing space",
   safe_filename("y" * 79 + " zzz", ".mp4"), "y" * 79 + ".mp4")
eq("quotes and pipes", safe_filename('a"b|c<d>e:f*g?h', ".mp4"), "a b c d e f g h.mp4")

if fails:
    print(f"FAIL ({len(fails)})")
    for f in fails:
        print(" -", f)
    sys.exit(1)
print("all pure-function tests pass")
