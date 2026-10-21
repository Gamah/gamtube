"""The /d/ ephemeral routes, end to end through FastAPI's TestClient.

Needs fastapi, httpx and jinja2. Skips with a notice when they aren't
installed, so `make test` still works on a bare checkout. Point PYTHONPATH at
a --target install if you don't want a venv.

Run: python3 tests/test_routes.py
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

try:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
except ImportError as exc:
    print(f"skipped: {exc.name} not installed")
    raise SystemExit(0)

from bot.agents import DEFAULT_CRAWLERS
from bot.store import Item, MemoryStore
from web.routers import ephemeral

fails = []


def eq(label, got, want):
    if got != want:
        fails.append(f"{label}\n   got:  {got!r}\n   want: {want!r}")


def ok(label, cond):
    if not cond:
        fails.append(label)


# A real-shaped crawler UA and a real-shaped browser UA.
CRAWLER_UA = "Mozilla/5.0 (compatible; Discordbot/2.0; +https://discordapp.com)"
HUMAN_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/140.0 Safari/537.36")

BODY = bytes(range(256)) * 8  # 2048 bytes, every value, so slices are checkable

store = MemoryStore(ttl_seconds=100, max_bytes=10_000)
store.put("abc123", Item(
    data=BODY,
    content_type="video/mp4",
    title="A Clip",
    width=1280,
    height=720,
    duration=42,
    source_url="https://www.tiktok.com/@u/video/1",
))

app = FastAPI()
app.state.bot_store = store
app.include_router(ephemeral.router)
client = TestClient(app)


class FakeSettings:
    """Stands in for BotSettings so each gate can be exercised."""

    def __init__(self, human_notice=True, media_user_agents=(), bot_debug=False):
        self.human_notice = human_notice
        self.bot_debug = bot_debug
        self._media = media_user_agents

    def crawlers(self):
        return DEFAULT_CRAWLERS

    def media_agents(self):
        return self._media


_real = ephemeral.get_settings_bot


def settings(**kw):
    ephemeral.get_settings_bot = lambda: FakeSettings(**kw)


def _as(ua, path, headers=None, **kw):
    return client.get(path, headers={"User-Agent": ua, **(headers or {})}, **kw)


def crawl(path, **kw):
    return _as(CRAWLER_UA, path, **kw)


def human(path, **kw):
    return _as(HUMAN_UA, path, **kw)


try:
    settings()

    # ---- the page a crawler sees ----
    r = crawl("/d/abc123")
    eq("page status", r.status_code, 200)
    html = r.text
    ok("og:video present", '<meta property="og:video"' in html)
    ok("og:video points at the media route", 'content="http://localhost/d/abc123/f"' in html)
    ok("og:video:type", 'property="og:video:type"' in html and "video/mp4" in html)
    ok("og:video:width", 'property="og:video:width"  content="1280"' in html)
    ok("og:video:height", 'property="og:video:height" content="720"' in html)
    ok("og:video:duration", 'content="42"' in html)
    ok("og:title", 'property="og:title" content="A Clip"' in html)
    ok("canonical url", 'property="og:url"              content="http://localhost/d/abc123"' in html)

    # ---- the page a person sees ----
    r = human("/d/abc123")
    eq("notice status", r.status_code, 200)
    ok("notice mentions Discord", "for Discord" in r.text)
    ok("notice has no og:video", "og:video" not in r.text)
    ok("notice has no video element", "<video" not in r.text)
    ok("notice links the original", "tiktok.com/@u/video/1" in r.text)

    # An absent UA counts as a crawler: curl and some proxies send nothing, and
    # treating unknown as human would silently cost embeds.
    r = client.get("/d/abc123", headers={"User-Agent": ""})
    ok("empty UA gets og tags", "og:video" in r.text)

    # ---- notice disabled ----
    settings(human_notice=False)
    ok("human gets og tags when notice is off", "og:video" in human("/d/abc123").text)
    settings()

    eq("unknown key 404s", crawl("/d/nope").status_code, 404)
    ok("gone page says so", "expired" in crawl("/d/nope").text)

    # ---- full media fetch ----
    r = crawl("/d/abc123/f")
    eq("media status", r.status_code, 200)
    eq("media body", r.content, BODY)
    eq("media type", r.headers["content-type"], "video/mp4")
    eq("accept-ranges advertised", r.headers["accept-ranges"], "bytes")
    eq("humans get bytes by default", human("/d/abc123/f").status_code, 200)

    # ---- range requests, which is how Discord actually fetches ----
    r = crawl("/d/abc123/f", headers={"Range": "bytes=0-99"})
    eq("range status", r.status_code, 206)
    eq("range body", r.content, BODY[:100])
    eq("content-range", r.headers["content-range"], f"bytes 0-99/{len(BODY)}")
    eq("range length", r.headers["content-length"], "100")

    r = crawl("/d/abc123/f", headers={"Range": "bytes=1000-"})
    eq("open-ended status", r.status_code, 206)
    eq("open-ended body", r.content, BODY[1000:])
    eq("open-ended content-range", r.headers["content-range"],
       f"bytes 1000-{len(BODY)-1}/{len(BODY)}")

    r = crawl("/d/abc123/f", headers={"Range": "bytes=-64"})
    eq("suffix body", r.content, BODY[-64:])

    r = crawl("/d/abc123/f", headers={"Range": "bytes=99999-"})
    eq("unsatisfiable status", r.status_code, 416)
    eq("unsatisfiable content-range", r.headers["content-range"], f"bytes */{len(BODY)}")

    r = crawl("/d/abc123/f", headers={"Range": "items=0-10"})
    eq("unknown unit falls back to 200", r.status_code, 200)
    eq("unknown unit sends everything", r.content, BODY)

    # ---- HEAD, which some clients send before fetching ----
    r = client.head("/d/abc123/f", headers={"User-Agent": CRAWLER_UA})
    eq("head status", r.status_code, 200)
    eq("head advertises length", r.headers["content-length"], str(len(BODY)))
    eq("head has no body", r.content, b"")

    eq("missing key media 404s", crawl("/d/nope/f").status_code, 404)

    # ---- the optional byte gate ----
    # Deliberately a separate list from the crawler one: Discord's media proxy
    # is not its unfurler, so this is filled from the log, not assumed.
    settings(media_user_agents=("mediaproxy",))
    eq("listed agent gets bytes",
       client.get("/d/abc123/f", headers={"User-Agent": "Discord-MediaProxy/1.0"}).status_code,
       200)
    eq("human blocked from bytes", human("/d/abc123/f").status_code, 403)
    eq("even the page crawler is blocked from bytes when unlisted",
       crawl("/d/abc123/f").status_code, 403)
    eq("gate leaves the page alone", human("/d/abc123").status_code, 200)
    settings()
    eq("empty list serves anyone", human("/d/abc123/f").status_code, 200)
    eq("empty list serves crawlers too", crawl("/d/abc123/f").status_code, 200)

    # ---- an app with no store configured must not 500 ----
    bare = FastAPI()
    bare.state.bot_store = None
    bare.include_router(ephemeral.router)
    # debug header dumping must not change any response
    settings(bot_debug=True)
    eq("debug page still 200", crawl("/d/abc123").status_code, 200)
    eq("debug media still 200", crawl("/d/abc123/f").status_code, 200)
    eq("debug range still 206",
       crawl("/d/abc123/f", headers={"Range": "bytes=0-9"}).status_code, 206)
    settings()

    eq("no store configured 404s",
       TestClient(bare).get("/d/abc123", headers={"User-Agent": CRAWLER_UA}).status_code,
       404)
finally:
    ephemeral.get_settings_bot = _real

# ---- the logger must actually be able to emit INFO ----
# Uvicorn leaves the root logger alone, so without web.main._configure_logging
# every gamtube.bot INFO record is dropped by logging.lastResort at WARNING.
import logging  # noqa: E402

from web.logsetup import configure_logging  # noqa: E402

bot_log = logging.getLogger("gamtube.bot")
eq("INFO is dropped before configuring", bot_log.isEnabledFor(logging.INFO), False)
configure_logging(debug=False)
eq("INFO emits after configuring", bot_log.isEnabledFor(logging.INFO), True)
eq("DEBUG stays off at info level", bot_log.isEnabledFor(logging.DEBUG), False)
configure_logging(debug=True)
eq("debug=True raises the level", bot_log.isEnabledFor(logging.DEBUG), True)
eq("repeat calls add no handlers", len(logging.getLogger("gamtube").handlers), 1)

if fails:
    print(f"FAIL ({len(fails)})")
    for f in fails:
        print(" -", f)
    sys.exit(1)
print("all route tests pass")
