"""The pure parts of the bot: link detection and filename/title handling.

No token, no network, no venv - these are the only pieces of the bot that can
be exercised without a live gateway, which is why they live outside the
discord.py handlers. Run: python3 tests/test_pure.py
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from bot.detect import extract_candidates, is_candidate, norm_url, unembedded
from bot.agents import DEFAULT_CRAWLERS, is_crawler
from bot.ranges import parse_range
from bot.scope import allowed_by_ids, missing_capabilities, opted_in
from bot.store import Item, MemoryStore
from core.titles import clean_title

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

# ---- scope: the coarse allowlists ----
eq("no allowlists allows all", allowed_by_ids(1, 2, set(), set()), True)
eq("guild allowlist hit", allowed_by_ids(1, 2, {1}, set()), True)
eq("guild allowlist miss", allowed_by_ids(9, 2, {1}, set()), False)
eq("channel allowlist hit", allowed_by_ids(1, 2, set(), {2}), True)
eq("channel allowlist miss", allowed_by_ids(1, 9, set(), {2}), False)
eq("dm rejected when guilds listed", allowed_by_ids(None, 2, {1}, set()), False)
eq("dm allowed with no allowlist", allowed_by_ids(None, 2, set(), set()), True)
eq("both must pass", allowed_by_ids(1, 9, {1}, {2}), False)


# ---- scope: opt-in by explicit permission overwrite ----
class Ov:
    """Stand-in for PermissionOverwrite: unset permissions read as None."""
    def __init__(self, **kw):
        self.__dict__.update(kw)

    def __getattr__(self, name):
        return None


class Role:
    def __init__(self, name):
        self.name = name


class Guild:
    def __init__(self, default_role):
        self.default_role = default_role


class Me:
    def __init__(self, roles, guild):
        self.roles = roles
        self.guild = guild


class Chan:
    def __init__(self, grants=None):
        self._grants = grants or {}

    def overwrites_for(self, obj):
        return self._grants.get(obj, Ov())


class Thread:
    def __init__(self, parent):
        self.parent = parent


everyone = Role("@everyone")
botrole = Role("gamtube")
guild = Guild(everyone)
me = Me([everyone, botrole], guild)

eq("explicit member grant",
   opted_in(me, Chan({me: Ov(embed_links=True)}), "embed_links"), True)
eq("explicit bot-role grant",
   opted_in(me, Chan({botrole: Ov(embed_links=True)}), "embed_links"), True)
eq("@everyone grant does not opt in",
   opted_in(me, Chan({everyone: Ov(embed_links=True)}), "embed_links"), False)
eq("no overwrites at all",
   opted_in(me, Chan(), "embed_links"), False)
eq("explicit deny is not a grant",
   opted_in(me, Chan({botrole: Ov(embed_links=False)}), "embed_links"), False)
eq("a different permission does not count",
   opted_in(me, Chan({botrole: Ov(send_messages=True)}), "embed_links"), False)
eq("configurable permission name",
   opted_in(me, Chan({botrole: Ov(send_messages=True)}), "send_messages"), True)
eq("thread inherits its parent's grant",
   opted_in(me, Thread(Chan({botrole: Ov(embed_links=True)})), "embed_links"), True)
eq("thread with no parent grant",
   opted_in(me, Thread(Chan()), "embed_links"), False)
eq("missing member object",
   opted_in(None, Chan({botrole: Ov(embed_links=True)}), "embed_links"), False)
eq("channel without overwrites_for",
   opted_in(me, object(), "embed_links"), False)
eq("guild with no default_role still checks roles",
   opted_in(Me([botrole], Guild(None)), Chan({botrole: Ov(embed_links=True)}),
            "embed_links"), True)


# ---- ranges ----
SIZE = 1000
eq("no header", parse_range(None, SIZE), None)
eq("empty header", parse_range("", SIZE), None)
eq("explicit range", parse_range("bytes=0-99", SIZE), (0, 99))
eq("open-ended range", parse_range("bytes=100-", SIZE), (100, 999))
eq("suffix range", parse_range("bytes=-500", SIZE), (500, 999))
eq("suffix longer than body", parse_range("bytes=-5000", SIZE), (0, 999))
eq("end clamped to body", parse_range("bytes=0-99999", SIZE), (0, 999))
eq("last byte", parse_range("bytes=999-999", SIZE), (999, 999))
eq("whitespace tolerated", parse_range("bytes= 10-20 ", SIZE), (10, 20))
eq("wrong unit ignored", parse_range("items=0-10", SIZE), None)
eq("multi-range ignored", parse_range("bytes=0-10,20-30", SIZE), None)
eq("garbage ignored", parse_range("bytes=abc", SIZE), None)
eq("missing dash ignored", parse_range("bytes=10", SIZE), None)
eq("inverted range ignored", parse_range("bytes=50-10", SIZE), None)
eq("empty body", parse_range("bytes=0-10", 0), None)

for label, header in [("start past end", "bytes=1000-1010"),
                      ("open-ended past end", "bytes=1000-"),
                      ("far past end, open-ended", "bytes=99999-"),
                      ("zero suffix", "bytes=-0")]:
    try:
        parse_range(header, SIZE)
        fails.append(f"{label}: expected ValueError, got a value")
    except ValueError:
        pass


# ---- store ----
class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def item(n_bytes, **kw):
    return Item(data=b"x" * n_bytes, content_type="video/mp4", **kw)


clock = Clock()
store = MemoryStore(ttl_seconds=10, max_bytes=1000, now=clock)

store.put("a", item(100, title="A"))
got = store.get("a")
eq("roundtrip", got.title if got else None, "A")
eq("size reported", store.total_bytes(), 100)
eq("missing key", store.get("nope"), None)
eq("contains", "a" in store, True)

eq("fetch counter", store.get("a").fetches, 3)  # contains + the two gets above

clock.t = 9.0
eq("alive before ttl", store.get("a") is not None, True)
clock.t = 11.0
eq("expired after ttl", store.get("a"), None)
eq("expiry frees bytes", store.total_bytes(), 0)

# eviction: oldest first, once over budget
store = MemoryStore(ttl_seconds=100, max_bytes=250, now=clock)
store.put("one", item(100))
store.put("two", item(100))
store.put("three", item(100))
eq("oldest evicted", store.get("one"), None)
eq("newer kept", store.get("two") is not None, True)
eq("newest kept", store.get("three") is not None, True)
eq("within budget", store.total_bytes() <= 250, True)

# an item larger than the whole budget does not wedge the store
store = MemoryStore(ttl_seconds=100, max_bytes=100, now=clock)
store.put("huge", item(500))
eq("oversized item dropped", len(store), 0)

# re-putting refreshes position, so it is no longer the eviction candidate
store = MemoryStore(ttl_seconds=100, max_bytes=250, now=clock)
store.put("x", item(100))
store.put("y", item(100))
store.put("x", item(100))
store.put("z", item(100))
eq("refreshed key survives", store.get("x") is not None, True)
eq("stale key evicted", store.get("y"), None)

# ttl of 0 disables expiry
store = MemoryStore(ttl_seconds=0, max_bytes=1000, now=clock)
store.put("forever", item(10))
clock.t += 10_000
eq("ttl 0 never expires", store.get("forever") is not None, True)


# ---- crawler detection (the PAGE gate only; the media proxy is a separate
# service whose agent we don't assume) ----
eq("discord unfurler",
   is_crawler("Mozilla/5.0 (compatible; Discordbot/2.0; +https://discordapp.com)"), True)
eq("telegram", is_crawler("TelegramBot (like TwitterBot)"), True)
eq("slack", is_crawler("Slackbot-LinkExpanding 1.0"), True)
eq("case insensitive", is_crawler("DISCORDBOT/2.0"), True)
eq("chrome is a person",
   is_crawler("Mozilla/5.0 (Macintosh) AppleWebKit/537.36 Chrome/140.0 Safari/537.36"), False)
eq("safari is a person",
   is_crawler("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0) Version/18.0 Mobile Safari/604.1"), False)
eq("curl is a person", is_crawler("curl/8.0"), False)
eq("missing UA treated as crawler", is_crawler(None), True)
eq("empty UA treated as crawler", is_crawler(""), True)
eq("custom list replaces the default",
   is_crawler("Discordbot/2.0", ("telegrambot",)), False)
eq("custom list matches its own", is_crawler("TelegramBot", ("telegrambot",)), True)
ok = "discordbot" in DEFAULT_CRAWLERS
if not ok:
    fails.append("DEFAULT_CRAWLERS must contain discordbot")


# ---- eviction reporting: whether an item died before Discord fetched it is
# the thing we need to see while tuning the TTL ----
seen = []
store = MemoryStore(ttl_seconds=10, max_bytes=250, now=clock,
                    on_evict=lambda k, i, why: seen.append((k, why, i.fetches)))
clock.t = 0.0
store.put("a", item(100))
store.get("a")
store.put("b", item(100))
store.put("c", item(100))
eq("budget eviction reported", seen, [("a", "budget", 1)])

seen.clear()
clock.t = 100.0
store.prune()
eq("ttl evictions reported", sorted(w for _, w, _ in seen), ["ttl", "ttl"])

# a raising callback must not break eviction
store = MemoryStore(ttl_seconds=10, max_bytes=100, now=clock,
                    on_evict=lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
store.put("x", item(50))
clock.t += 1000
eq("callback failure is contained", store.get("x"), None)


# ---- missing_capabilities: all four permissions are per-channel now, so a
# half-ticked channel must be reported rather than silently doing nothing ----
class Perms:
    def __init__(self, **kw):
        self.__dict__.update(kw)

    def __getattr__(self, name):
        return False


class PermChan:
    def __init__(self, perms):
        self._perms = perms

    def permissions_for(self, me):
        return self._perms


ALL_FOUR = dict(view_channel=True, send_messages=True,
                read_message_history=True, embed_links=True)

eq("fully granted channel", missing_capabilities(me, PermChan(Perms(**ALL_FOUR))), [])
eq("missing send_messages",
   missing_capabilities(me, PermChan(Perms(**{**ALL_FOUR, "send_messages": False}))),
   ["send_messages"])
eq("missing embed_links",
   missing_capabilities(me, PermChan(Perms(**{**ALL_FOUR, "embed_links": False}))),
   ["embed_links"])
eq("nothing granted",
   missing_capabilities(me, PermChan(Perms())),
   ["view_channel", "send_messages", "read_message_history", "embed_links"])
eq("custom requirement set",
   missing_capabilities(me, PermChan(Perms(embed_links=True)), ("embed_links",)), [])
eq("channel without permissions_for", missing_capabilities(me, object()), [])
eq("no member object", missing_capabilities(None, PermChan(Perms())), [])


# ---- byte accounting: the bot waits on this before posting the link, so
# "has discord finished pulling it" has to be right ----
it = item(1000)
eq("starts incomplete", it.complete, False)
it.record_served(400)
eq("partial stays incomplete", (it.served_bytes, it.complete), (400, False))
it.record_served(600)
eq("reaching size completes", (it.served_bytes, it.complete), (1000, True))

it = item(1000)
it.record_served(1000, whole=True)
eq("full-body response completes", it.complete, True)

it = item(1000)
it.record_served(50, whole=True)
eq("whole flag completes regardless of count", it.complete, True)

it = item(1000)
it.record_served(-5)
eq("negative counts ignored", it.served_bytes, 0)

it = item(1000)
for _ in range(3):
    it.record_served(500)
eq("overlapping ranges still complete", it.complete, True)

store = MemoryStore(ttl_seconds=100, max_bytes=10_000, now=clock)
store.put("k", item(300))
eq("store reports incomplete", store.is_complete("k"), False)
store.record_fetch("k", 100)
eq("still incomplete", store.is_complete("k"), False)
store.record_fetch("k", 200)
eq("store reports complete", store.is_complete("k"), True)
store.record_fetch("gone", 100)
eq("recording an unknown key is harmless", store.is_complete("gone"), False)

if fails:
    print(f"FAIL ({len(fails)})")
    for f in fails:
        print(" -", f)
    sys.exit(1)
print("all pure-function tests pass")
