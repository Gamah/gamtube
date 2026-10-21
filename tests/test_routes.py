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

from bot.store import Item, MemoryStore
from web.routers import ephemeral

fails = []


def eq(label, got, want):
    if got != want:
        fails.append(f"{label}\n   got:  {got!r}\n   want: {want!r}")


def ok(label, cond):
    if not cond:
        fails.append(label)


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

# ---- the unfurled page ----
r = client.get("/d/abc123")
eq("page status", r.status_code, 200)
html = r.text
ok("og:video present", '<meta property="og:video"' in html)
ok("og:video points at the media route", 'content="http://localhost/d/abc123/f"' in html)
ok("og:video:type", 'property="og:video:type"' in html and "video/mp4" in html)
ok("og:video:width", 'property="og:video:width"  content="1280"' in html)
ok("og:video:height", 'property="og:video:height" content="720"' in html)
ok("og:video:duration", 'content="42"' in html)
ok("og:title escaped and present", 'property="og:title" content="A Clip"' in html)
ok("canonical url", 'property="og:url"              content="http://localhost/d/abc123"' in html)

eq("unknown key 404s", client.get("/d/nope").status_code, 404)
ok("gone page says so", "expired" in client.get("/d/nope").text)

# ---- full media fetch ----
r = client.get("/d/abc123/f")
eq("media status", r.status_code, 200)
eq("media body", r.content, BODY)
eq("media type", r.headers["content-type"], "video/mp4")
eq("accept-ranges advertised", r.headers["accept-ranges"], "bytes")

# ---- range requests, which is how Discord actually fetches ----
r = client.get("/d/abc123/f", headers={"Range": "bytes=0-99"})
eq("range status", r.status_code, 206)
eq("range body", r.content, BODY[:100])
eq("content-range", r.headers["content-range"], f"bytes 0-99/{len(BODY)}")
eq("range length", r.headers["content-length"], "100")

r = client.get("/d/abc123/f", headers={"Range": "bytes=1000-"})
eq("open-ended status", r.status_code, 206)
eq("open-ended body", r.content, BODY[1000:])
eq("open-ended content-range", r.headers["content-range"], f"bytes 1000-{len(BODY)-1}/{len(BODY)}")

r = client.get("/d/abc123/f", headers={"Range": "bytes=-64"})
eq("suffix body", r.content, BODY[-64:])

r = client.get("/d/abc123/f", headers={"Range": "bytes=99999-"})
eq("unsatisfiable status", r.status_code, 416)
eq("unsatisfiable content-range", r.headers["content-range"], f"bytes */{len(BODY)}")

r = client.get("/d/abc123/f", headers={"Range": "items=0-10"})
eq("unknown unit falls back to 200", r.status_code, 200)
eq("unknown unit sends everything", r.content, BODY)

# ---- HEAD, which some clients send before fetching ----
r = client.head("/d/abc123/f")
eq("head status", r.status_code, 200)
eq("head advertises length", r.headers["content-length"], str(len(BODY)))
eq("head has no body", r.content, b"")

eq("missing key media 404s", client.get("/d/nope/f").status_code, 404)

# ---- the optional user-agent gate ----
class FakeBotSettings:
    serve_user_agents = "discordbot, telegrambot"


_real = ephemeral.get_settings_bot
ephemeral.get_settings_bot = lambda: FakeBotSettings()
try:
    eq("matching UA allowed",
       client.get("/d/abc123/f", headers={"User-Agent": "Mozilla/5.0 (compatible; Discordbot/2.0)"}).status_code,
       200)
    eq("non-matching UA blocked",
       client.get("/d/abc123/f", headers={"User-Agent": "curl/8.0"}).status_code,
       403)
    eq("gate does not touch the page",
       client.get("/d/abc123", headers={"User-Agent": "curl/8.0"}).status_code,
       200)
finally:
    ephemeral.get_settings_bot = _real

eq("gate off again", client.get("/d/abc123/f", headers={"User-Agent": "curl/8.0"}).status_code, 200)

# ---- an app with no store configured must not 500 ----
bare = FastAPI()
bare.state.bot_store = None
bare.include_router(ephemeral.router)
eq("no store configured 404s", TestClient(bare).get("/d/abc123").status_code, 404)

if fails:
    print(f"FAIL ({len(fails)})")
    for f in fails:
        print(" -", f)
    sys.exit(1)
print("all route tests pass")
