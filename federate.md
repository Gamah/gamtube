# gamtube federation

**Status:** design document. Nothing here is implemented.
**Date:** 2026-07-28.

This describes turning gamtube from a single-server re-hoster into a federation of
self-hosted nodes that replicate video between themselves over BitTorrent and serve it to
viewers over HTTP.

The protocol is meant to be **rigid from the start**. The implementation will move a lot;
the identity model, the layering, and the ingest rules should not. Decisions are recorded
here with their reasoning so a future session doesn't re-litigate them from scratch.

---

## 1. Why

gamtube today is one box that downloads, stores, and serves every byte. That works and it
should keep working. The reason to federate is not scale and not cost — it's that the
people who would run this already own always-on servers with large disks, and pooling them
gets you something a single instance can't have: content that survives its origin.

The thesis is narrow and worth stating plainly, because it rules things in and out:

> Watch and share video without being tracked, on infrastructure we already own.

Not piracy. Not anonymity. Not a commercial CDN. We are not trying to evade detection — we
are trying to avoid surveillance, which is a different problem with different solutions.
That distinction is why several decisions below come out permissive where a piracy-oriented
design would come out paranoid.

**Why homelab nodes and not browser peers.** An earlier version of this idea had browsers
seeding to each other via WebTorrent. Browsers are bad peers: they can't hold a connection
once the tab closes, they can only reach other WebRTC peers (a swarm disjoint from mainline
BitTorrent), they have no DHT, and a meaningful share of peer pairs need a TURN relay that
someone has to pay for. Worst of all, peers retain only the pieces they watched, and
everyone watches from the start — so swarms rot from the end, and videos start failing to
seek long before they fail to play.

An always-on server has none of those problems. It holds whole files, it has uptime, it has
a routable port, and its operator already understands bandwidth caps. Mobile clients are
pure consumers and that's fine.

---

## 2. What this is, and what it isn't

It is **a volunteer CDN over a content-addressed catalog**. Nodes replicate bytes to each
other with BitTorrent; viewers fetch over ordinary HTTP.

It is not a tube platform, not a social network, and not an archive with guarantees.

**Videos are not guaranteed to work. That is the design, not a defect.** The network is a
cache. The original source platform is unreliable cold storage behind it. A video that
nobody keeps alive becomes unavailable, and a video whose source has also vanished is gone
for good. Everything in this document follows from taking that seriously rather than
apologising for it.

---

## 3. Identity model

Two identifiers, doing two different jobs. Conflating them is the most likely way to get
this wrong.

| | derived from | names | stable across re-ingest |
|---|---|---|---|
| `short_id` | the source URL | *the video* | yes |
| infohash | the bytes | *these exact bytes* | no |

`short_id = SHA256(canonical(source_url))[:12]` — already implemented in `app/ids.py`.

The relationship is git's: `short_id` is a branch name, the infohash is a commit sha.
`/v/{short_id}` resolves to whatever the current edition is and may trigger a re-ingest.
`/v/{short_id}/{infohash}` pins exact bytes forever.

### Why not derive `short_id` from the infohash

Tempting — the name would then mean exactly one byte stream, self-verifying, no coordination
needed. It breaks three things that matter more:

- **You can't compute it before downloading.** Today `POST /submit` derives the id from the
  URL and returns an existing one instantly with zero work. Content-derived ids force
  download-then-name, and duplicate detection needs a separate URL→id index anyway.
- **Divergence becomes invisible.** Two instances ingesting the same URL currently produce
  the same `short_id`, so the system can see they're the same video. Content-derived, they'd
  produce unrelated ids with nothing linking them.
- **Re-ingest orphans every old link.** Cold→warm resurrection is core to the design: the
  swarm dies, the source URL still resolves, the video comes back. Under URL-derived ids the
  id survives that. Under content-derived ids the new bytes get a new name and every link
  ever shared is permanently dead with no forwarding pointer.

### Editions

One `short_id` maps to N infohashes over time. Each is an **edition**. Editions are
first-class in the protocol from day one — cheap now, impossible to retrofit.

Editions arise from re-ingest (source re-fetched after the swarm died), re-canonicalisation
(moving to a new codec years from now), and archival raw copies. A `short_id` on its own is
not servable; resolving it to an edition is the *only* job it has.

### URL canonicalisation — protocol-level, rigid

`youtube.com/watch?v=X`, `youtu.be/X`, `m.youtube.com/watch?v=X`, and any of those with
`&t=30s` or tracking params must all reduce to one `short_id`. A canonicalisation ruleset is
applied before hashing and is **part of the protocol, not an implementation detail.**

Today a divergent URL costs one duplicate download. In a federation it fragments swarms
permanently: a different `short_id` means the pre-ingest lookup misses, so both instances
ingest and neither can help the other. Changing the ruleset later renames every video in the
network, so if it must ever change it has to be versioned.

The requirement is locked. The ruleset itself is open — see §11.

---

## 4. Protocol layering

Layer by rate of change, and never let a fast-changing signal into a slow-changing channel.

| layer | carries | mechanism | changes |
|---|---|---|---|
| catalog | `short_id` → source URL, edition list | signed mutable DHT record | rarely |
| liveness | who has this infohash right now | mainline DHT | per play |
| bytes | the video itself, node↔node | BitTorrent | continuously |
| playback | the video itself, node→viewer | HTTP range / HLS | per play |

**Peer discovery is the mainline BitTorrent DHT, unapologetically.** It's large, proven,
needs no bootstrap infrastructure from us, and keeps working if every gamtube instance goes
down. The cost is that announces are public and enumerable. Given the thesis in §1 —
anti-tracking, not anti-detection, no commercial interest — that's an acceptable trade, and
pretending otherwise with a private DHT would buy a small keyspace that's cheap to eclipse.

**Delivery to viewers is deliberately not BitTorrent.** Using BitTorrent for playback means
fighting sequential piece selection against rarest-first, and it means a custom player on
every client. Using it only for node↔node replication gives it exactly the job it's
excellent at — efficient many-to-many bulk transfer of immutable content — while viewers get
plain HTTP where seeking, range requests, and `<video>` all just work.

**Consequence, stated explicitly:** because the swarm is on the mainline DHT, any stock
BitTorrent client can fetch anything in it, with no reference to our catalog and no auth. A
node's "serve only registered clients" setting throttles *that node's HTTP bandwidth*. It is
**not access control**. Unlist and delete cannot claw back anything that has reached a second
seeder. Nobody should later mistake the auth toggle for privacy.

### Catalog: no federation protocol

No ActivityPub, no follow graph, no signed instance feeds, no inbox/outbox. Instances are
independent. The link is the entry.

What federation would have bought us — avoiding duplicate ingest, and propagating edition
updates so old links don't die — is bought instead by putting a small signed record in the
DHT, keyed by `short_id`, containing the source URL and the current edition list. Same
benefit, no new protocol, using infrastructure we're already joining. Records decay unless
republished, which is on-thesis: content nobody keeps alive stops being resolvable.

`[SOURCE]` The mutable-record mechanism is BEP 44. Its size limit and republish interval must
be read from the BEP directly before any numbers are written into an implementation — they
are not recorded here from memory.

### The record is self-validating

`short_id` → source URL is not invertible, so an instance handed a `short_id` it didn't
ingest cannot know where to re-fetch from. The source URL therefore travels *inside* the
record.

Nice property: the record is keyed by `short_id`, so any reader can check
`SHA256(canonical(record.source_url))[:12] == key`. A record physically cannot lie about
which URL it describes, with no signature needed for that field.

This publishes source URLs to the public DHT. Consistent with the mainline decision above.

---

## 5. Ingest

### Ingest once, replicate bytes — never recompute

Before ingesting, an instance looks up `short_id` in the DHT. If an edition already exists,
it replicates those exact bytes over BitTorrent instead of re-downloading from the source.

This rule is **separate from** the canonical-encoding rule below, and neither substitutes for
the other. Mandating an encoding profile does not produce identical bytes: encoder output is
not bit-identical across ffmpeg/x264 versions, build flags, or thread counts, and container
muxing adds its own drift. Two nodes running the identical profile on the identical source
still produce two infohashes and two disjoint swarms. Only ingest-once produces one swarm.

### Canonical form — penciled in, open to revision

Every federated video is normalised at ingest to **H.264 / AAC in fragmented MP4**, with a
pinned profile.

The argument is universal playback. H.264 plays on every browser, phone, and TV; AV1 and VP9
do not, and software AV1 decode on an older phone is a battery fire. For a product whose core
action is "send a friend a link," that outweighs the costs — which are real: generational
quality loss from re-encoding VP9/AV1 sources, a larger file at equal quality (so volunteers
donate more disk and upload for a worse-looking video), and encode CPU on hardware that is
often an N100 or an old Xeon.

This is the decision most likely to change. The profile specifics are open (§11).

### Reproducible ingest is not a viable path

Worth recording so it isn't re-proposed. Some nondeterminism is removable: pin an exact
format id rather than `bestvideo+bestaudio/best`, disable metadata and thumbnail embedding,
and avoid the ffmpeg merge step entirely by fetching a single progressive file or storing
video and audio streams unmerged — those bytes are exactly what the CDN served.

What is not removable is that the platform can change what it serves. Formats appear and
disappear, and platforms re-encode their own renditions server-side, with no notification.
`[SOURCE]` — that is read from observed platform behaviour, not a documented contract; check
yt-dlp's issue tracker before building on it.

So determinism is achievable between instances ingesting around the same time, and not across
months. It is also mutually exclusive with the canonical rendition, since transcoding is
inherently non-reproducible. Convergence comes from the pre-ingest lookup, not from
reproducibility.

---

## 6. Node model

A node advertises capabilities. Neither side of a transfer is forced to expose itself or to
trust anyone.

- **direct-serve** — serves HTTP to viewers. Requires a routable port, TLS, and abuse
  protection, and exposes the operator's IP to every viewer.
- **replicate-only** — holds bytes and feeds other nodes over BitTorrent, never faces the
  public.

Clients choose too: pull only from their home node, or pull from the swarm directly (native
clients only — browsers have no BitTorrent). The viewer's instance relays when no direct-serve
node has the content; relay is a fallback, not the default, because a relaying instance pays
full video bitrate.

**Storage is plaintext and operators curate.** We are not protecting anyone's bespoke content,
so encryption-at-rest would buy a legally untested deniability claim at the cost of the thing
that actually makes operators comfortable: choosing their own shelf. Operators set pin scopes
— mirror this submitter, this tag, this curator — and can see and refuse anything.

**Client auth is per-node registration.** A user has one home server. No cross-node key
distribution, no federated identity, no tokens crossing trust boundaries. If multi-server
friction turns out to bite, the escape hatch is home-instance-vouched signed tokens: the
instance issues a short-lived signature, other nodes verify against its published key. Not
built now.

Operators get the knobs they already expect from qBittorrent: global and per-torrent rate
caps, connection limits, time-of-day scheduling, disk quota, pin count cap, and per-requester
bandwidth ceilings so one viewer can't drain a node.

---

## 7. Availability states

Not two states. A cache hierarchy.

| state | meaning | recovery |
|---|---|---|
| **warm** | seeds online | plays now |
| **cold** | no seeds, source URL still resolves | any instance can re-ingest → new edition |
| **gone** | no seeds, source dead | nothing to do |

That middle state is the advantage over every pure-P2P archive, and gamtube already
implements it: `app/routers/videos.py` flips an `expired` row back to `pending` and re-enqueues
`process_video`. Expiry stops being deletion and becomes eviction.

UI consequence: **availability is a first-class fact, not a spinner that eventually 404s.**
Show "4 seeds", "cold — waking", "gone, last seen March 2026". And a link handed to a friend
must land on a page that works even when the video doesn't: provenance, source link, a wake
button. A shared link that dead-ends silently is what would kill this by word of mouth.

---

## 8. Pin economics

A "like" pins the video to **the liker's own instance** for 24 hours, drawn against a
per-user pin budget, within a total the operator has consented to donate.

The important detail is that a like never causes a write on someone else's disk. There is no
cross-instance griefing vector, the cost lands on storage the operator opted into, and a
finite budget makes "keep this alive" a decision rather than a reflex.

Replication then emerges without a central scheduler: popular content lands on many instances
and plays instantly; niche content lives on one and plays slowly; an archivist runs a curator
instance that pins by policy regardless of likes.

**Likes never federate.** They are local to the instance and no per-user signal leaves it. The
only globally visible popularity measure is swarm size, which BitTorrent leaks anyway. This is
the anti-tracking thesis applied to our own telemetry.

Existing prior art in the codebase: videos marked permanent (`expires_at` null, surfaced in
the `/scroll` feed) are already operator-pinned content under a different name.

---

## 9. Impact on the current codebase

| file | change |
|---|---|
| `app/ids.py` | `short_id_for()` already is the work identifier. Gains URL canonicalisation before hashing. |
| `app/models.py` | `Video` gains edition linkage; a new `Edition` table (infohash, profile, size, created_at) replaces the single `video_path` / `file_size_bytes` pair. |
| `app/pipeline/worker.py` | `process_video` becomes ingest-or-replicate. `reencode_video` becomes edition minting. |
| `app/pipeline/downloader.py` | Pinned-format, no-remux ingest. |
| `app/storage/base.py` | The `StorageBackend` ABC is the right seam for a torrent-backed backend; `get_local_path()` already exists for handing files to a seeder. |
| `app/routers/videos.py` | The `expired` → re-enqueue path is already cold→warm resurrection — extend, don't rebuild. |

Unrelated drift noticed while writing this: `Video.transcoded` is `String(10)` in
`app/models.py` but documented as Boolean in `CLAUDE.md`. Worth reconciling whenever that
column is next touched.

---

## 10. Roadmap

Each phase states what it proves and what it leaves broken.

**1. Seed what we already host.** Create torrents for stored files, announce to the mainline
DHT, expose magnet links alongside the existing `/v/{id}` links.
*Proves:* the seeding leg, on one node, with no protocol changes.
*Broken:* no second node, no federation, nothing replicates.

**2. Editions in the data model.** Infohash column, `Edition` table, URL canonicalisation,
links carrying both ids, canonical profile at ingest.
*Proves:* the identity model survives contact with the existing schema.
*Broken:* editions still only ever have one member.

**3. Replicate between two nodes.** A second instance fetches an edition by infohash instead
of re-downloading from source.
*Proves:* ingest-once-replicate-bytes.
*Broken:* discovery is manual — you tell node B the infohash by hand.

**4. HTTP delivery with verification.** Signed manifest, per-segment hash check in a service
worker, multi-node range fetch with mid-stream failover.
*Proves:* the browser leg, including that a malicious node can't substitute bytes.
*Broken:* still no automatic node discovery.

**5. DHT catalog records.** Pre-ingest lookup by `short_id`, edition-update propagation,
republish loop.
*Proves:* convergence without a federation protocol.
*Broken:* no accounts, so no pin budgets.

**6. Accounts, pin budgets, availability UI.** Per-node registration, like-to-pin,
warm/cold/gone surfaced in the player.
*Proves:* the social layer and the storage economics.

---

## 11. Open questions

Deliberately unresolved. Each needs a decision before the phase that depends on it.

- **Canonical profile specifics** — codec profile and level, fragment duration, resolution
  ceiling. A ceiling makes donated disk go further but bakes a lossy choice into ingest.
- **Raw edition retention** — declined as a default (double storage). Archival-nodes-only is
  the compromise on the table, and it's what would let a future re-canonicalisation avoid
  generational loss.
- **BEP 44 record size limit and republish interval** — read from the BEP, do not assume.
- **BitTorrent v2 (BEP 52, SHA-256 merkle trees) vs v1 (SHA-1)** — v2's per-block merkle
  proofs fit segment verification well and SHA-1 is weak against deliberate collisions, but
  v2 client support is narrower. Verify current support before committing.
- **URL canonicalisation ruleset** — per-platform rules, tracking-param stripping, and whether
  it's versioned so it can ever change without renaming the catalog. The *requirement* is
  locked; the ruleset is open.
- **Home-instance-vouched tokens** — the later alternative to per-node registration.
- **Relay fallback** — when it engages, who pays, how it's capped.

---

## 12. Risks

- **Instance-admin burnout is the leading cause of death in the fediverse**, and here a dead
  instance takes its pins with it. Availability tracks admin enthusiasm decay, which is faster
  than anyone plans for.
- **Node population will be geographically lumpy** — a small, correlated hobbyist demographic
  concentrated in a few countries and timezones. The fallback for an underserved region is the
  origin instance.
- **Legal posture changes with the leap.** Hosting your own library for friends and accepting
  arbitrary public submissions for strangers are materially different positions. The operator
  onboarding has to say so in plain words, not in a ToS nobody reads.
- **DHT announces are public and permanent.** Anything that reaches a second seeder cannot be
  recalled. Design moderation around removing catalog records and refusing to serve, not around
  deletion.
- **Byte verification only covers the payload.** BitTorrent proves the bytes match the
  infohash; it cannot prove the infohash is the video the catalog promised. That binding needs
  signing regardless of transport.

---

## Conventions

Claims recalled rather than re-derived from a spec are marked `[SOURCE]` and must be verified
before implementation. Statements about third-party platform behaviour are inference from
observation unless a documented contract is cited.
