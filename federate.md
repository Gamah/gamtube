# gamtube federation

**Status:** design document. Nothing here is implemented.
**Date:** 2026-07-28, revised repeatedly the same day. The largest revision deleted the catalog
layer outright: nodes never resolve `short_id` → infohash over the network, which removed the
only place a node would have weighed a stranger's claim, and with it quorum, corroboration
counting and sampled verification. Protocol citations are read from the BEPs directly rather
than recalled; determinism claims are measured, see Appendix A.

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

It is **a volunteer CDN over content-addressed video**. Nodes replicate bytes to each
other with BitTorrent; viewers fetch over ordinary HTTP.

It is not a tube platform, not a social network, and not an archive with guarantees.

The governing principle, from which most of the rest follows: **the network exists to replicate
what was served, and `URL → short_id → infohash` is king.** Nothing is processed, normalised,
or improved on the way in. Where playback convenience and the identity chain conflict, the
identity chain wins and playback is solved locally (§5).

**gamtube should die early.** It is not trying to become a platform, and a design that fails
cleanly is preferred to one kept alive by accretion. Several decisions here are only defensible
under that stance — no recovery path for a dead link, no catalog, no adjudication when nodes
disagree, and a platform that serves inconsistent bytes simply breaking the design rather than
being worked around. Each of those is somewhere a contributor could helpfully add resilience and
in doing so rebuild the thing this deliberately isn't. When a change's justification is "so it
keeps working in more cases," that is the signal to check it against this paragraph.

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

Editions arise from re-ingest (source re-fetched after the swarm died), a change to the pinned
config, and the divergence exceptions in §5. A `short_id` on its own is not servable; resolving
it to an edition is the *only* job it has.

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
| identity | `short_id` + infohash | carried in the link itself | never |
| liveness | who has this infohash right now | mainline DHT | per play |
| bytes | the video itself, node↔node | BitTorrent | continuously |
| playback | the video itself, node→viewer | HTTP range over a locally-muxed file | per play |

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
BitTorrent client can fetch anything in it, with no reference to gamtube at all and no auth. A
node's "serve only registered clients" setting throttles *that node's HTTP bandwidth*. It is
**not access control**. Unlist and delete cannot claw back anything that has reached a second
seeder. Nobody should later mistake the auth toggle for privacy.

### There is no catalog

No ActivityPub, no follow graph, no signed feeds, no rendezvous, no `short_id` lookup of any
kind. Instances are independent and **the link is the entry.**

A shareable link carries `short_id` *and* infohash. That is sufficient, because a node only
ever needs to answer one of two questions:

1. **Is the source URL up?** Then derive it — pull from the platform (§5). Nothing else is
   consulted.
2. **Is the source URL down, and was I handed an infohash?** Then fetch that infohash over
   ordinary BitTorrent. Standard BEP 5 peer discovery on the real infohash; no synthetic
   targets, no custom protocol.

Both questions are answered without asking anyone what a `short_id` means. That is the whole
design, and everything below follows from it.

**Earlier drafts had a catalog layer and it has been deleted.** It existed to resolve
`short_id` → infohash for a node that had neither the source nor the infohash. That capability
was the *single* point where a node would have accepted a stranger's claim, and it dragged an
entire apparatus behind it: quorum thresholds, derivation-versus-holder accounting, sampled
recomputation against the CDN, and a defence against revival-squatting on long-expired ids.
Removing one lookup removes all of it.

Recorded so neither is re-proposed: a **BEP 44 mutable record keyed by `short_id` cannot
work** — a mutable item's target is "the SHA-1 hash of the public key (as it appears in the put
message)" plus salt, so a reader needs the publisher's key and cannot derive the address from a
URL. A **BEP 5 rendezvous on a synthetic target does work** technically — the spec places no
constraint on the 20-byte value — but it is exactly the lookup we no longer want.

The cost, stated plainly: **a bare `short_id` whose source URL is dead is unavailable.** No
lookup, no recovery, no exception. Links carry infohashes for precisely this reason.

### Why no verification is needed

Because a node only ever fetches an infohash it was *handed*, there is no moment at which it
accepts a stranger's assertion about what content is.

**BitTorrent pins the bytes.** Every block is checked against the merkle tree; the infohash is
the content. A rogue node cannot inject different bytes into a swarm under someone else's
infohash — it would have to break the hash function. It can refuse to serve, stall, or lie about
what it holds, none of which substitutes content. The failure modes are availability failures,
not integrity failures.

So there is no verification step, no quorum, no recomputation-on-adopt, no derivation counting,
and no sampled range-requesting of the source CDN. Those all existed to make a `short_id`
lookup safe, and the lookup is gone.

**Determinism is still load-bearing, but for a different reason.** It is what makes two nodes
independently deriving the same URL arrive at the same swarm rather than fragmenting into
separate ones. If a platform starts serving different byte streams for the same URL, this design
breaks — and that is accepted rather than defended against. Patching around it would mean
rebuilding exactly the trust machinery just deleted. See §12.

### Rogue nodes

Nodes are anonymous and anyone can run one, so assume a hostile fraction. Their reach is bounded
by the fact that every fetch is infohash-addressed.

**They cannot substitute bytes** — see above.

**They cannot serve a viewer they have no relationship with.** Per §6, direct-serve means
serving *that node's own registered clients*. Strangers serve other nodes, over BitTorrent. A
viewer's bytes come from the node they registered with, frequently their own hardware.

That is what makes serve-time muxing safe. A locally-muxed file is *by construction*
unverifiable against the raw streams' tree — remuxing changes byte sequences, sizes and offsets,
so no mapping back to the block hashes exists — but the node performing the mux already
validated the raw bytes against the swarm, and it is a node the viewer chose. Buying protection
against your own chosen node would cost an MSE player on every client, forfeiting the
plain-HTTP dumb-client property that justified not delivering over BitTorrent at all. A client
wanting end-to-end verification fetches the raw streams and muxes them itself — a native-client
option, never the default.

**The one open surface: abandoned swarms.** A rogue can watch for a `short_id`/infohash pair
with few or no seeders and start seeding *its own* file. It cannot serve that file under the
original infohash, so it cannot poison an existing link — but it can occupy the space around a
dying video and, over time, be the only thing still answering. Worth tracking rather than
solving now: a later concern, recorded here so it isn't forgotten.

---

## 5. Ingest

### The acquisition rule

There are two cases and no others.

**If the source URL is up, always pull from the platform.** Every node, every time, no
exceptions and nothing consulted first. Deterministic ingest means independent pulls land on the
same infohash and therefore the same swarm, so the network converges without coordinating.

**If the source URL is down, fetch the infohash you were handed, over BitTorrent.** No
verification, no corroboration, no quorum — the infohash pins the bytes, and a rogue would have
to break the hash function to substitute anything (§4).

That is the entire policy. A node never asks anyone what a `short_id` means, so there is no
third case in which it would weigh someone else's claim.

The practical trigger for the second case is a request arriving at a node for content it doesn't
hold. Rate limiting, region blocks and a spent extraction budget also count as "the source is
not available to me," and need no special handling — they simply mean the node can't derive.

**Why pull upstream when the infohash is already in hand?** Not for correctness — fetching a
held infohash over BitTorrent is exactly as trustworthy as deriving, since the infohash pins
the bytes either way. The reasons are freshness and independence. A network that preferred
peers would drift into a stale mirror: every node holding a two-year-old edition while the
platform quietly serves something else. "Replicate what was served" means *what is served now*.
Deriving also keeps a node's ability to obtain live content independent of swarm health.

**This is a default, not an enforceable constraint.** A node that fetches a held infohash from
peers instead produces byte-identical results, so the deviation is invisible to everyone and
breaks nothing. An operator on a metered or fragile connection can quietly do so. The rule is a
statement about being a good citizen of the network, not a security requirement — worth knowing,
because it means no mechanism is needed to police it.

Note also what the DHT can and cannot answer, since this is where a catalog gets reinvented: it
is keyed **by** infohash and answers *"who has this,"* never *"what is this."* A node holding an
infohash needs no lookup to use it; a node without one cannot obtain it from the network at all.

### When a link names an infohash and the source is also up

These can disagree: the link names `infohash_A`, the node derives `infohash_B` because the
platform re-encoded. The acquisition rule governs what a node *stores*; it does not govern what
a viewer *receives*, and conflating the two makes this look like a contradiction.

The resolution order, from the one rule worth protecting — **never silently substitute**:

1. **The link's infohash is a request for those exact bytes.** If they are obtainable — the node
   holds them, or the swarm has seeders — serve them. This is what content addressing is for,
   and the requester saw that content.
2. **If they are not obtainable, the node does not pretend.** It may offer its own current
   derivation, but as an explicitly different thing, marked as not the bytes the link named.
   Silently serving `infohash_B` under a link that said `infohash_A` would forfeit the only
   guarantee the identity model provides.
3. **Deriving still happens regardless**, per the acquisition rule. It is the node's own ingest,
   not an attempt to satisfy the link.

The practical effect is that a shared link keeps meaning what it meant, and a video the platform
has since re-encoded shows up as two editions rather than one silently swapped one.

### There is no canonical form

> Determinism is what makes this section work; it is established under "Ingest is deterministic
> by construction" below.


**The network exists to replicate what was served.** An edition is exactly the bytes yt-dlp
retrieved under the pinned config — verbatim, unprocessed, in whatever shape the platform
delivered them. `URL → short_id → infohash` is the spine of the design, and nothing is allowed
to bend it.

Concretely, that means the edition is a **multi-file torrent** whenever the pinned format is
DASH: a video-only stream and an audio-only stream, both raw CDN bytes. BitTorrent v2 handles
multi-file torrents natively with per-file merkle trees, so this costs nothing.

We deliberately do *not* pin a progressive single-file format to make playback tidier. On
YouTube the only combined-stream format observed in Appendix A's test was itag 18 — 240p for
that video, and around 360p generally — so choosing it would trade most of the quality away to
avoid a serve-time remux. Format selection is a decision about *what is worth replicating*,
never about what is convenient to play.

### Playback is a local concern, not a protocol concern

The distinction that makes this work: **muxing at ingest destroys convergence; muxing at serve
time is free.** Only replicated bytes are hashed.

So a node serving a viewer may remux the two raw streams into a playable container (a stream
copy — no re-encode, cheap), or transcode to H.264 for an older client that can't decode VP9 or
AV1. That output is **local, cached, disposable, regenerable, never hashed, never announced,
and never an edition.** Two nodes producing byte-different muxes of the same edition is fine,
because nothing compares them.

This removes the canonical rendition from the protocol entirely. There are no derived editions,
no "produced once then copied, never recomputed" rule, no second replicated artifact, and no
transcode CPU on the ingest path. Compatibility becomes something each node solves for its own
viewers, at its own cost, however it likes.

**The remux cache is a second copy, and it is budgeted separately.** A stream-copied mux is
roughly the size of the streams it combines, so a 2 GB edition implies about 2 GB more on disk.
That is not a hidden cost inside the pin quota:

- **Pins are commitments to the network. Cache is local convenience.** They get separate
  budgets, and the operator sees both.
- **Cache evicts first, and aggressively.** It is regenerable from bytes the node already holds,
  so losing it costs CPU, never content. A node under disk pressure empties cache entirely
  before touching a single pin.
- **Cache never counts as contributing.** It is not announced, not seeded, and not visible to
  the replication floor in §8.

An operator who would rather spend CPU than disk can run with no cache at all and mux per
request; one serving mostly-compatible clients may need very little cache, since a client that
can decode the raw streams needs no mux.

### Cold-start playback while a fetch is in flight

A viewer can ask for content the node doesn't hold yet. BitTorrent's rarest-first piece
selection is what keeps a swarm healthy, but it means the local file has no usable prefix until
completion — so a naive implementation would block playback until 100%.

The split: **viewer-triggered fetches request pieces sequentially; background replication uses
rarest-first.** Sequential mode is standard in torrent libraries, and the swarm-health cost is
bounded because it applies only to fetches with someone actually waiting, which are a minority
of transfers. Background pinning, the bulk of traffic, stays rarest-first.

Until enough of a prefix exists, the viewer gets the progress page rather than a stalled player
— machinery the project already has in `status.html` and the SSE progress endpoint, which was
built for exactly this wait. This is the cold→warm transition in §7 with a swarm behind it
instead of a platform.

### Ingest is deterministic by construction

There is exactly one client consuming these platforms: **yt-dlp, configured by the gamtube
server.** The configuration is ours and uniform across the fleet, so the usual sources of
byte variance — client fingerprint, format preference, container handling — are not variables.
They are constants we choose.

**Pinned by the protocol,** not left to the implementation:

- An explicit format id per platform. Never `bestvideo+bestaudio/best`.
- The yt-dlp player client, pinned explicitly. `[SOURCE]` yt-dlp selects among several player
  clients internally and its defaults shift between releases; confirm the exact extractor-arg
  against yt-dlp's documentation before use.
- No remux, no metadata embedding, no thumbnail embedding. Fetch a single progressive file, or
  store the video and audio streams unmerged — those bytes are exactly what the CDN served,
  with no ffmpeg anywhere in the path.
- **Never fall back.** If the pinned format is unavailable, the ingest *fails*. Silently
  selecting the next-best format is the single most likely way honest nodes would diverge.
- **A canonical file tree.** BEP 52 places the file tree in the info dict, so names and order
  feed the infohash directly — identical bytes under different filenames produce different
  infohashes. yt-dlp emits dynamic extensions (`.m4a`, `.weba`, `.webm`, `.mp4`) that vary by
  format and version, so the outputs are **renamed to fixed positional names before hashing**:
  `0` for the video stream, `1` for the audio stream, no containing directory. Order is by
  stream role, never by yt-dlp's output order. Note that Appendix A measured *payload* bytes,
  not metainfo — this rule is unmeasured and is the most likely remaining source of silent
  divergence between honest nodes.
- **The JavaScript runtime is part of the pinned config.** Its presence changes which formats
  are visible at all — current yt-dlp warns that without one "some formats may be missing" —
  so the fleet standardises on it exactly as it does on the format id.
- **Deterministic torrent creation:** fixed piece length, fixed info-dict field set, canonical
  file naming, no `creation date` and no `created by`. This makes the infohash a pure function
  of the bytes.

With that config fixed, two nodes handed the same URL derive **the same infohash**.
Convergence is the expected outcome, not a lucky one — measured, not assumed; see Appendix A.

Version skew across the fleet is not a source of byte divergence once the format id and player
client are explicit. The version governs extraction *ability*, not the bytes of a named
format, and this was measured: two yt-dlp releases nine months apart produced byte-identical
output for every format tested. Where the older release could not cope with a modern video it
returned HTTP 403 and produced nothing. **Version skew fails closed** — a node that cannot
extract mints no edition rather than a wrong one.

**Three exceptions remain, and they are the entire reason editions exist:**

1. **Region** — format availability and access differ geographically regardless of client.
2. **Platform re-encoding** — a platform regenerating a rendition changes the bytes for
   everyone, silently. `[SOURCE]`, observed behaviour rather than documented.
3. **Format withdrawal** — a pinned format id can cease to exist. Because there is no
   fallback, this fails the ingest rather than diverging it, and is resolved by changing the
   pinned config, which mints a new edition on purpose rather than by accident.

Note what is *not* on this list: node identity, node honesty, and node acquaintance. Nothing a
node claims can affect another node's bytes, because every fetch is infohash-addressed (§4).

Divergence between nodes is therefore not a security event — it is a swarm-fragmentation event.
Two honest nodes that derive different bytes end up in different swarms for the same video,
which costs replication and nothing else. **Provenance travels with every edition** so this can
be diagnosed rather than guessed at: yt-dlp version, format id, player client, ingest timestamp,
ingest region. Differing provenance explains most divergence; identical provenance yielding
different bytes means the platform changed underneath.

Provenance is diagnostic only, never a security claim, since it is self-reported (§12).

### Why nothing may be processed at ingest

Encoding rules and byte identity are **separate concerns**. Mandating an encoding profile does
not produce identical bytes: encoder output is not bit-identical across ffmpeg/x264 versions,
build flags, or thread counts, and container muxing adds its own drift. Determinism comes from
the ingest config, never from the encoder.

A transcode is not reproducible; neither is a mux. Any ffmpeg step in the ingest path breaks
`URL → infohash` for every node that runs a different build, which is all of them eventually.

So the rule is absolute rather than a default: **ffmpeg never touches a replicated artifact.**
This reverses the project's current `TRANSCODE_ENABLED` behaviour, where transcoding at ingest
is a supported option — under federation it cannot be, because the resulting bytes could never
converge with anyone else's.

### Edition churn is self-cleaning

When gamtube changes its pinned config — a new format id, a new player client — the same URL
starts yielding different bytes and therefore a new infohash. **This needs no migration and no
arbitration.** Old editions stop being ingested, stop being liked, lose their seeders, and fall
off disk and out of the network. The network drifts onto whatever the current config produces
because that is what new ingests produce.

So there is deliberately no edition-reconciliation machinery: no canonical-edition election, no
version negotiation, no rewriting of old records. Obsolete editions are garbage collected by
disinterest.

The one visible cost: a link that named a specific infohash breaks when that edition dies. It
degrades gracefully rather than dead-ending, because the link also carries `short_id` — the
receiving instance re-ingests under the current config and mints a fresh edition. That is the
cold→warm path in §7 doing its job.

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

A "like" pins the video to **the liker's own instance**, drawn against a per-user pin budget,
within a total the operator has consented to donate.

The important detail is that a like never causes a write on someone else's disk. There is no
cross-instance griefing vector, the cost lands on storage the operator opted into, and a
finite budget makes "keep this alive" a decision rather than a reflex.

**Retention is capacity-bound LRU, not a flat TTL.** A fixed 24-hour pin looks reasonable and
fails badly: everyone who likes a video during its viral hour has their pin expire during the
same hour a day later, so seed count falls off a cliff rather than decaying. Correlated
expiry is how a swarm dies all at once.

Instead: the operator donates a capacity, and pins are evicted least-recently-used when it
fills. Three refinements on top —

- **Jitter** any time component so nothing expires in lockstep.
- **Replication floor before eviction.** Before dropping an edition, check swarm size via the
  DHT; if evicting would take it below a floor, keep it and evict the next candidate instead.
  This costs one DHT lookup and directly counters the cliff.
- **Repeated likes extend rather than duplicate**, so sustained interest keeps something warm
  without consuming more budget than it needs.

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
| `app/pipeline/downloader.py` | Pinned-format, no-remux ingest; never falls back to another format. |
| `app/pipeline/transcoder.py` | Moves off the ingest path entirely. Becomes serve-time remux/transcode into a local disposable cache — `TRANSCODE_ENABLED=true` is incompatible with federation. |
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
links carrying both ids, multi-file editions, no ffmpeg in the ingest path.
*Proves:* the identity model survives contact with the existing schema.
*Broken:* editions still only ever have one member.

**3. Two nodes, independently ingested.** Both instances pull the same URL from upstream and
compare infohashes; then a third fetches by infohash from peers instead of upstream.
*Proves:* independent convergence in the wild, and that peer replication works as the fallback.
*Broken:* discovery is manual — you tell the nodes about each other by hand.

**4. HTTP delivery.** Serve-time remux of the raw streams into a playable file, cached locally.
Byte-range requests over that, with seeking. No HLS — there is no ABR ladder to need it.
*Proves:* the browser leg, on ordinary `<video>` with no custom player.
*Broken:* still no automatic node discovery.

**There is deliberately no client-side verification here.** A remuxed file cannot be checked
against the raw streams' merkle tree — remuxing changes byte sequences, sizes and offsets, so no
mapping back to the block hashes exists. Verification happens at the node, which validates
everything it pulls from the swarm before muxing; the browser trusts the node it registered
with (§4, Rogue nodes). A client that wants end-to-end verification fetches the raw streams and
muxes them itself with MSE — a native-client option, never the default.

**5. Fallback fetch by infohash.** A request arrives for content the node doesn't hold and whose
source URL is dead; the node fetches the infohash from the link over ordinary BitTorrent, with
an extraction budget and backoff governing when it decides the source is unavailable to it.
*Proves:* the cold path, with no catalog and no lookup.
*Broken:* no accounts, so no pin budgets.

**6. Accounts, pin budgets, availability UI.** Per-node registration, like-to-pin,
warm/cold/gone surfaced in the player.
*Proves:* the social layer and the storage economics.

---

## 11. Open questions

Deliberately unresolved. Each needs a decision before the phase that depends on it.

- **Format selection per platform** — which format id to pin. Now purely a question of what is
  worth replicating (quality against donated disk and upload), since playback compatibility is
  handled locally at serve time and no longer constrains the choice.
- **Serve-time remux caching** — cache size defaults relative to pin quota, and whether a mux is
  built on first play or per request. The *policy* is settled in §5 (separate budget, evicts
  first, never counts as contributing); the numbers are not.
- **Canonical file tree, verified** — the positional-naming rule in §5 is unmeasured. Building
  torrents from identical streams on two yt-dlp versions and comparing infohashes is a cheap
  test and it guards the most likely remaining source of silent divergence.
- **Extraction budget** — how many upstream resolves a node performs per hour and how it backs
  off on 429, i.e. when it concludes the source is unavailable to it and falls to the infohash
  path. Needs real numbers from observed platform behaviour, not a guess.
- **BitTorrent v2 (BEP 52) vs v1** — v2's SHA-256 merkle trees are stronger than v1's SHA-1,
  which is weak against deliberate collisions, and multi-file editions get per-file trees. The
  open part is v2 support in whatever torrent stack we embed. Less critical than it was once
  sampled verification was removed, but the collision argument stands on its own.
- **Whether platforms other than YouTube are deterministic at all.** Appendix A tests YouTube
  only. Instagram and TikTok re-encode far more aggressively; if they don't converge, every
  node ends up in its own swarm for that platform and replication stops working there.
- **Regional divergence in practice** — untested. If two regions routinely yield different bytes
  for the same format id, the network fragments along regional lines.
- **URL canonicalisation ruleset** — per-platform rules, tracking-param stripping, and whether
  it's versioned so it can ever change without renaming everything. The *requirement* is locked;
  the ruleset is open.
- **Abandoned-swarm squatting** — tracking rogue nodes that seed their own file alongside a
  dying `short_id` (§4). Deferred, deliberately.
- **Does `short_id` still earn its place, and what exactly does a link carry?** These are one
  question, not two, and both are deferred.

  With the catalog gone, `short_id` has lost its main job. What remains is a stable name for
  "the video at this URL" across editions, plus instant duplicate detection at submit — and both
  of those are satisfied by a purely *local* index key, derived from the URL, that never needs to
  appear in the protocol.

  The sharper problem: `short_id` is not invertible. A node handed only a `short_id`, whose swarm
  is dead and whose source is alive, cannot recover the URL and therefore cannot re-derive — the
  one recovery that ought to work. That suggests a link should carry **source URL + infohash**,
  with `short_id` demoted to an implementation detail.

  Settle that before specifying link format, since it determines what is being encoded. The
  format decisions waiting behind it: fragment versus path (a `#` fragment never reaches the
  server, so a node cannot log which edition a viewer asked for — material for an anti-tracking
  project), encoding of a 64-hex-character v2 infohash (base32 ≈ 52 chars, base64url ≈ 43), and
  what a link missing its infohash does — currently nothing, per §2.
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
  recalled. Design moderation around refusing to serve, not around deletion.
- **Platform rate limiting is the ceiling on "always pull from the platform."** Fifty instances
  each pulling the same video upstream is exactly the pattern that earns a 429 or a block, and
  homelab IPs have no headroom. When platforms push back, nodes fall to the infohash path, which
  works — but only for content whose links carry an infohash, and only while somebody still
  seeds it.
- **If a platform serves different byte streams for the same URL, this design breaks.** Every
  node lands in its own swarm, replication stops working for that platform, and the network
  degenerates into unrelated instances that each downloaded the same video separately. Note the
  penalty falls hardest exactly where it hurts most: a heavily-requested video is the one whose
  swarm most needs to be pooled, and fluctuating edge encodings would shatter it into many
  single-seeder swarms while obscure content — fetched once, rarely re-derived — is unaffected.
  This is
  **accepted, not defended against.** Every available defence — corroboration, quorum,
  reputation, adjudicating which edition is canonical — reintroduces exactly the trust machinery
  this design exists without. If it happens at scale it is a bigger conversation about whether
  the approach is viable, not a patch.
- **A malicious operator is unavoidable and already assumed.** An operator controls their own
  machine and can fabricate bytes directly; DNS spoofing or MITM of their own outbound traffic
  buys them nothing extra. It also gains them nothing, because fabricated bytes have a different
  infohash and no link points at it. This is why provenance fields (§5) are diagnostic only and
  never a security claim.

---

## Appendix A — measured ingest determinism

Run 2026-07-28 on one host, using standalone yt-dlp release binaries. Method: download a
single pinned format with no remux and no metadata embedding, then `sha256sum` the media file.

| test | formats | result |
|---|---|---|
| yt-dlp 2026.07.04, three consecutive runs | 18 | identical |
| yt-dlp 2026.07.04, two runs each | 140, 251 | identical |
| **2025.10.22 vs 2026.07.04**, static 2005 video | 18, 140, 251 | **identical** |
| 2026.07.04, two runs, modern video | 140 | identical |
| 2025.10.22, modern video | 140 | **HTTP 403 — no output** |

Conclusions drawn: byte determinism for a *named* format holds across a nine-month version
gap; version skew fails closed rather than diverging; and format *availability* — not format
content — is what varies, driven by extraction capability and JavaScript-runtime presence.

Explicitly not tested, and not to be implied: regional variance (single host, single IP),
platform re-encoding over time (the 2005 video is near-maximally static), and any extractor
other than YouTube.

**The next test worth running**, and the cheapest attack on the assumption the whole design
rests on: a second machine on a different network and in a different region deriving the same
URL under the same pinned config. Regional variance is currently pure assumption in both
directions — nothing here shows it exists, and nothing here shows it doesn't.

---

## Conventions

Claims recalled rather than re-derived from a spec are marked `[SOURCE]` and must be verified
before implementation. Statements about third-party platform behaviour are inference from
observation unless a documented contract is cited. Protocol claims cite the BEP they were read
from; empirical claims cite Appendix A.
