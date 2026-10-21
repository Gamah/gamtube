#!/usr/bin/env python3
"""Summarise what happened to each link the bot posted.

One line per key, built from the bot's own log lines, to answer the questions
nobody can answer from Discord's side:

  full=   did Discord ever pull the whole file from us? A partial copy cannot
          be completed once our link 404s.
  404s=   requests that arrived after the item was gone. These are Discord (or
          a viewer) re-fetching at render time, which is what a broken-image
          placeholder in an old embed looks like. If these exist, Discord is
          not serving the video from a cache of its own.
  evict=  when we dropped it, and whether it was complete at that point.

Usage: python3 scripts/bot-timeline.py [path/to/app.log]
"""

import collections
import re
import sys

LOG = sys.argv[1] if len(sys.argv) > 1 else "/opt/gamtube/logs/app.log"

TS = r"^(\d{4}-\d\d-\d\d )?(?P<ts>\d\d:\d\d:\d\d)"
PATTERNS = {
    "posted": re.compile(TS + r".*posted (?P<url>\S+) as \S+/d/(?P<key>\w+) "
                              r"\((?P<mb>[\d.]+)MB, (?P<dim>\S+), held"),
    "page": re.compile(TS + r".*page (?P<key>\w+) crawler=(?P<crawler>\w+) "
                            r"ua='(?P<ua>[^']*)'"),
    "media": re.compile(TS + r".*media (?P<key>\w+) (?P<method>GET|HEAD) "
                             r"fetch=(?P<n>\d+) range=(?P<range>\S+) "
                             r"ua='(?P<ua>[^']*)'"),
    "full": re.compile(TS + r".*media (?P<key>\w+) fully served"),
    "gone": re.compile(TS + r".*media (?P<key>\w+) 404"),
    "page_gone": re.compile(TS + r".*page (?P<key>\w+) 404"),
    "evict": re.compile(TS + r".*store evict (?P<key>\w+) \((?P<why>\w+)\).*"
                             r"complete=(?P<complete>\w+)"),
    "evict_old": re.compile(TS + r".*store evict (?P<key>\w+) \((?P<why>\w+)\) "
                                 r"after (?P<n>\d+) fetch"),
    "warm": re.compile(TS + r".*warm (?P<key>\w+): discord pulled the whole file "
                            r"in (?P<secs>[\d.]+)s"),
}


def blank():
    return {
        "posted": None, "mb": None, "dim": None, "url": None,
        "pages": 0, "page_404": 0, "media": 0, "head": 0, "media_404": 0,
        "first_404": None, "full": None, "evict": None, "complete": None,
        "why": None, "warm": None, "uas": collections.Counter(),
        "ranges": collections.Counter(),
    }


keys = collections.defaultdict(blank)
order = []

try:
    lines = open(LOG, encoding="utf-8", errors="replace").read().splitlines()
except OSError as exc:
    sys.exit(f"cannot read {LOG}: {exc}")

for line in lines:
    if "gamtube.bot" not in line:
        continue
    for name, pat in PATTERNS.items():
        m = pat.search(line)
        if not m:
            continue
        key = m.group("key")
        rec = keys[key]
        if key not in order:
            order.append(key)
        ts = m.group("ts")
        if name == "posted":
            rec.update(posted=ts, mb=m.group("mb"), dim=m.group("dim"),
                       url=m.group("url"))
        elif name == "page":
            rec["pages"] += 1
            rec["uas"][m.group("ua")[:40]] += 1
        elif name == "page_gone":
            rec["page_404"] += 1
            rec["first_404"] = rec["first_404"] or ts
        elif name == "media":
            if m.group("method") == "HEAD":
                rec["head"] += 1
            else:
                rec["media"] += 1
            rec["uas"][m.group("ua")[:40]] += 1
            rec["ranges"][m.group("range")] += 1
        elif name == "full":
            rec["full"] = rec["full"] or ts
        elif name == "gone":
            rec["media_404"] += 1
            rec["first_404"] = rec["first_404"] or ts
        elif name == "warm":
            rec["warm"] = m.group("secs")
        elif name in ("evict", "evict_old"):
            rec["evict"] = ts
            rec["why"] = m.group("why")
            if name == "evict":
                rec["complete"] = m.group("complete")
        break

if not order:
    sys.exit(f"no gamtube.bot lines found in {LOG}")

print(f"{len(order)} link(s) in {LOG}\n")
for key in order:
    r = keys[key]
    print(
        f"{key}  posted={r['posted'] or '?':8} {str(r['mb']) + 'MB':>9} "
        f"{r['dim'] or '?':>10}  warm={r['warm'] or '-':>5}  "
        f"page={r['pages']}  media={r['media']}(head {r['head']})  "
        f"full={'yes@' + r['full'] if r['full'] else 'NO':>12}  "
        f"evict={(r['evict'] or '-') + '/' + (r['why'] or '-'):>14} "
        f"complete={r['complete'] or '?':5}  "
        f"404s={r['media_404']}+{r['page_404']}"
        + (f" from {r['first_404']}" if r['first_404'] else "")
    )

print("\n--- per-key user agents and ranges ---")
for key in order:
    r = keys[key]
    print(f"{key}: " + "; ".join(f"{ua!r} x{n}" for ua, n in r["uas"].most_common(4)))
    print(f"    ranges: " + ", ".join(f"{rg} x{n}" for rg, n in r["ranges"].most_common(6)))

done = [k for k in order if keys[k]["full"]]
never = [k for k in order if not keys[k]["full"]]
late = [k for k in order if keys[k]["media_404"] or keys[k]["page_404"]]
print(f"\nfully served: {len(done)}/{len(order)}   never complete: {len(never)}")
print(f"got requests after expiry: {len(late)}  -> {', '.join(late[:10]) or 'none'}")
if never:
    print(f"never complete: {', '.join(never[:10])}")
