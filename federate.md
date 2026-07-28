# gamtube federation

**Status:** design document. Nothing here is implemented.
**Date:** 2026-07-28. Revised the same day after external review — the catalog discovery
mechanism, the delivery/verification pairing, and the pin policy all changed. Protocol
citations below are now read from the BEPs directly rather than recalled.

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

The governing principle, from which most of the rest follows: **the network exists to replicate
what was served, and `URL → short_id → infohash` is king.** Nothing is processed, normalised,
or improved on the way in. Where playback convenience and the identity chain conflict, the
identity chain wins and playback is solved locally (§5).

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
| catalog | `short_id` → source URL, edition list | DHT rendezvous + HTTP query | rarely |
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
BitTorrent client can fetch anything in it, with no reference to our catalog and no auth. A
node's "serve only registered clients" setting throttles *that node's HTTP bandwidth*. It is
**not access control**. Unlist and delete cannot claw back anything that has reached a second
seeder. Nobody should later mistake the auth toggle for privacy.

### Catalog: no federation protocol

No ActivityPub, no follow graph, no signed instance feeds, no inbox/outbox. Instances are
independent. The link is the entry.

What federation would have bought us — avoiding duplicate ingest, and propagating edition
updates so old links don't die — is bought instead by using the DHT as a **rendezvous**, not
as a datastore. A node holding an edition of `short_id` announces itself under a synthetic
target:

```
catalog_target = SHA1("gamtube:catalog:v1:" + short_id)     # 20 bytes
```

`announce_peer` on that target, `get_peers` to resolve it. The result is a list of nodes that
claim to know this `short_id`; you then ask them over HTTP for the edition list.

Verified against BEP 5: `get_peers`/`announce_peer` take an arbitrary 20-byte value as
`info_hash`, and nothing in the spec requires it to correspond to a real torrent — "the
queried node should store the IP address of the querying node and the supplied port number
under the infohash in its store of peer contact information." BEP 5 specifies no TTL for
announced peer records, so expiry is implementation-defined and nodes must re-announce
periodically, which BitTorrent clients already do.

**Why not BEP 44,** recorded so it isn't re-proposed. An earlier draft put a signed mutable
record in the DHT keyed by `short_id`. That cannot work: per BEP 44 a mutable item's target is
"the SHA-1 hash of the public key (as it appears in the put message)" plus the salt if
present. A reader therefore needs the *publisher's Ed25519 public key* to compute the target,
and cannot derive it from a URL. With no federation graph there is nothing to distribute those
keys, and a shared keypair would let anyone overwrite the catalog. The salt cap (64 bytes) and
value cap (1000 bytes bencoded) are real but incidental — the addressing is the blocker.

Rendezvous also removes the size ceiling: the edition list is fetched over HTTP, not squeezed
into a 1000-byte DHT value.

Records still decay. A node that stops re-announcing stops being discoverable, and a
`short_id` nobody holds stops resolving. That is on-thesis.

### Verification is recomputation

`short_id` → source URL is not invertible, so an instance handed a `short_id` it didn't ingest
cannot know where to re-fetch from. The source URL therefore travels inside the catalog
answer, and that much is self-validating: any reader checks
`SHA256(canonical(answer.source_url))[:12] == short_id`. An answer cannot lie about which URL
it describes.

The infohash attached to it is a different matter, and the answer is **not** a trust graph.
Because ingest is deterministic (§5), the binding from URL to bytes to infohash is
*reproducible*, so **anyone can verify it by recomputing it.** No signatures, no identity, no
allowlist, no reputation. Nodes stay anonymous because nothing depends on who they are — only
on whether the bytes they advertise are the bytes the pinned config yields for that URL.

This is the footing reproducible builds stand on. It is not a proof you can hand a third party
— you cannot demonstrate to someone else what the platform served you without trusting your
report — but it is something every participant can check for themselves, which is what
actually matters here.

**Full verification costs as much as ingest**, so it is made cheap by sampling. With BitTorrent
v2 the merkle tree has 16 KiB leaf blocks (BEP 52: "the root hash of a merkle tree with a
branching factor of 2, constructed from 16KiB blocks of the file", SHA2-256), so a verifier can
range-request a random sample of blocks directly from the source CDN, hash them, and check them
against the tree. A few hundred KiB gives high confidence about a multi-hundred-MB file.
Substituting actual video content changes a large fraction of blocks and is caught; altering a
single block is not a useful attack against a media file. This is why v2 infohashes are close
to mandatory rather than optional here — see §11.

**Quorum is a cheap signal, not the foundation.** k nodes on independent network paths
advertising the same infohash is corroboration a verifier gets for free, and disagreement is
the flag worth surfacing — it means a regional difference, a platform re-encode, or a lying
node. But the security property is recomputation; quorum only saves you the trouble.

**What cannot be verified:** cold content whose source has gone. There is nothing to recompute
against, so an edition of a dead URL is trust-on-first-use or nothing. That is an honest limit,
and it is the same limit that makes such content unrecoverable in the first place (§7).

This publishes source URLs to the public DHT. Consistent with the mainline decision above.

---

## 5. Ingest

### Ingest once, replicate bytes — never recompute

Before ingesting, an instance looks up `short_id` in the DHT. If an edition already exists, it
replicates those exact bytes over BitTorrent instead of re-downloading from the source. This is
a bandwidth optimisation, not a correctness requirement — re-ingesting is always a valid
alternative, and under deterministic ingest it usually lands on the same infohash anyway.

Encoding rules and byte identity are **separate concerns**. Mandating an encoding profile does
not produce identical bytes: encoder output is not bit-identical across ffmpeg/x264 versions,
build flags, or thread counts, and container muxing adds its own drift. Determinism comes from
the ingest config (below), never from the encoder.

### There is no canonical form

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

Note what is *not* on this list: node identity, node honesty, and node acquaintance. A node
that lies about what the platform served it produces bytes that fail recomputation (§4).

Divergence is therefore the exception path, not the normal one. **Provenance travels with
every edition** so an exception can be diagnosed instead of guessed at: yt-dlp version, format
id, player client, ingest timestamp, ingest region. Identical provenance that yields different
bytes means the platform changed underneath; differing provenance disagreeing is expected.

### Why nothing may be processed at ingest

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

**3. Replicate between two nodes.** A second instance fetches an edition by infohash instead
of re-downloading from source.
*Proves:* ingest-once-replicate-bytes.
*Broken:* discovery is manual — you tell node B the infohash by hand.

**4. HTTP delivery with verification.** Serve-time remux of the raw streams into a playable
file, cached locally. Byte-range requests over that — no HLS, since there is no ABR ladder and
ranges map cleanly onto merkle boundaries. Per-block hash check in a service worker against the
v2 tree of the *raw* streams, multi-node range fetch with mid-stream failover.
*Proves:* the browser leg, including that a serving node can't substitute bytes.
*Broken:* still no automatic node discovery. Note the verification boundary — a viewer can
verify raw stream bytes, but a locally-muxed file is by definition unverifiable, so the check
belongs on the fetch path rather than the playback path.

**5. DHT rendezvous.** Announce and resolve on `catalog_target`, pre-ingest lookup by
`short_id`, HTTP edition-list exchange, re-announce loop, sampled recomputation check before
adopting a stranger's edition.
*Proves:* convergence and permissionless verification without a federation protocol.
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
- **Serve-time remux caching** — how long a muxed file is kept, and whether it's regenerated
  per request or on first play. Pure local resource management, invisible to the protocol.
- **BitTorrent v2 (BEP 52) is now close to mandatory,** not optional — sampled verification
  (§4) depends on 16 KiB merkle leaves, and v1's SHA-1 is weak against deliberate collisions.
  The open part is client and library support for v2 in whatever torrent stack we embed.
  Verify before committing; a v1 fallback loses cheap verification entirely.
- **Sampling parameters** — how many blocks, chosen how, and what confidence that yields
  against an adversary willing to alter a minority of the file.
- **Whether platforms other than YouTube are deterministic at all.** Appendix A tests YouTube
  only. Instagram and TikTok re-encode far more aggressively and may not converge; if they
  don't, they fall back to copy-the-bytes with no recomputation check available.
- **Regional divergence in practice** — untested. If two regions routinely yield different
  bytes for the same format id, quorum weakens to per-region quorum.
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
- **The whole identity model rests on ingest determinism.** If a platform stops being
  reproducible — or was never reproducible, as may be true outside YouTube — then
  recomputation stops working, quorum has nothing to agree on, and adopting a stranger's
  edition becomes trust-on-first-use. The mitigation is that self-ingest and human-shared
  links still need no trust; only stranger-edition adoption degrades, and it is optional.
- **A malicious operator is unavoidable and already assumed.** An operator controls their own
  machine, so they can fabricate bytes directly — DNS spoofing or MITM of their own outbound
  traffic buys them nothing extra. This is why provenance fields (§5) are diagnostic only and
  never a security claim, and why verification must always run on the *verifier's* own network
  path rather than being accepted as a reported result.

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

---

## Conventions

Claims recalled rather than re-derived from a spec are marked `[SOURCE]` and must be verified
before implementation. Statements about third-party platform behaviour are inference from
observation unless a documented contract is cited. Protocol claims cite the BEP they were read
from; empirical claims cite Appendix A.
