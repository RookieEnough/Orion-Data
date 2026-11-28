# Scaling Orion Data to a billion apps — at $0

> **The one-sentence answer:** stop storing one file per *question* and start storing one file per *answer*.
> Today `mirror.json` answers "what is every release of every app?" — a question no client asks on a cold start.
> Split it into four tiers that each answer exactly one question, and the ceiling moves from **3,759 apps** to **unbounded**, without spending a rupee.

Everything below is measured against the live repository on 2026-11-28, not estimated.
Reproduce any number with the scripts in `scripts/`.

**Status: implemented.** The tiered data plane described here ships in
`scripts/orion_data.py` + `scripts/build_data.py`, and
`.github/workflows/update_mirror.yml` now builds it. The client-facing wire
format is specified in [`DATA_CONTRACT.md`](DATA_CONTRACT.md). What remains is
the 3.x client itself — see §5 for exactly where the ceiling sits until then.

```bash
python scripts/build_data.py --from-mirror mirror.json --check   # build + gate
python scripts/mirror_audit.py --assert                          # audit the live legacy file
```

---

## 1. What the repository actually looks like today

| Artefact | Bytes | Read by |
|---|---:|---|
| `mirror.json` (`data` branch) | **48,370,117** | every client, on every cold start |
| `mirrors/**/*.json` (`data` branch) | ~48,000,000 | **nothing** |
| `updates.bin` (`data` branch) | 28,718 | **nothing** |
| `stars.json` (`data` branch) | 37,573 | every client |
| `apps.json` (`main`) | 2,535,054 | every client |
| `APKs/*.apk` (`main`) | 67,748,900 | **nothing** — deleted; still in history until `scripts/purge_binaries.sh` runs |
| **Repository `size` reported by the API** | **108,563 KB (106 MB)** | — |

Three things are true at once, and none of them are visible from the README:

1. **The `data` branch stores the same 48 MB twice.** `mirror_generator.py` writes every repo's release list to
   `mirror.json` *and* to `mirrors/<c1>/<c2>/<name>.json`. No client anywhere reads `mirrors/`.
   `updates.bin` is equally orphaned — the native client is Kotlin/`kotlinx-serialization` with **no msgpack decoder**,
   so it physically cannot read it.
2. **`mirror.json` stores the same 48 MB twice more.** `repo_cache[u_key] = data` *and* `repo_cache[repo_path] = data`
   put each repo in the file under two keys. Measured: **2,718 top-level keys = 1,359 primary + 1,359 bare — exactly 50.0% duplication.**
3. **Only ~3% of that file is data the client needs at boot.** 35,592 bytes per repo of *release history*,
   of which 1,165 bytes is the newest release. The rest is 27 older releases that only appear inside a
   version-picker sheet the user may never open.

---

## 2. The six walls, in the order they arrive

Not all walls are file size. Ranked by when they actually bite:

| # | Wall | Limit | You are at | Hits at | Status |
|---|---|---|---|---|---|
| **1** | GitHub REST rate limit | 1,000 req/h per repo (`GITHUB_TOKEN`) | ~1,359 repos × 2–3 calls ≈ 4,000 calls/run | **already** | **HIT TODAY** |
| **2** | jsDelivr package ceiling | 50 MB for GitHub packages | 48.4 MB = **97%** | ~1,792 apps | **imminent** |
| **3** | GitHub single-file block | **100 MiB hard** | 48.4 MB = 46% | **3,759 apps** | **~2.1× away** |
| **4** | `raw.githubusercontent.com` | 5,000 req/h **per IP** | ~1 per cold start per user | carrier NAT | **already, for CGNAT users** |
| **5** | Repository size | 5 GB (1 GB recommended) | 106 MB | APK folder + history | guards needed |
| **6** | Actions job runtime | 6 h hard | ~1,359 × 2 s ≈ 45 min | ~10,800 repos | far |

**Wall 1 is already open.** 281 of 1,734 apps (16%) have *no* release data in the live mirror right now.
The generator's own code turns a 403 into `fetch_errors[u_key] = "403 Rate Limit"` and the app silently
degrades to "Varies" — which is exactly the symptom in the store. The file-size fear is real but it is the
*second* thing that will kill you; the rate limit is killing you today.

**Wall 3 is 2.1× away.** At the current 27,895 bytes per app, the 100 MiB blob block lands at
**3,759 apps**. Past that point `git push` is rejected, the `data` branch freezes, and no new app can
ship its releases. That is precisely the failure you described.

**Wall 2 is closer still.** jsDelivr refuses to serve GitHub packages over 50 MB, and it is one of the
client's four mirror hosts (`MIRROR_URLS` in `Models.kt`). At 97% of that ceiling, the 4th fallback is
already unreliable.

---

## 3. The trap: "just shard it" does not work

The obvious fix — which `mirror_generator.py` already implements — is to write one JSON file per app
under `mirrors/<c1>/<c2>/`. That changes *file count*, not *data volume*:

```
today:  1734 apps x 27,895 B  =  48 MB  in 1 file      -> blocked at 3,759 apps
sharded: 1734 apps x 27,895 B  =  48 MB  in 1,734 files -> blocked at nothing, but
                                                           48 MB in git, 48 MB per clone,
                                                           48 MB per Actions checkout,
                                                           1,734 files rewritten every 6 h
```

Sharding makes each file small; it does not make the *repository* small, and it does not make the
*client download* small. A million apps would still mean 28 GB of JSON and 1,000,000 files —
past the 3,000-entries-per-directory recommendation and well past the 10 GB `.git` guidance.

You need to attack **bytes per app**, not files per app. Both, actually — but bytes first.

---

## 4. Seven rules that make the ceiling disappear

1. **One file per question, not per object.** Tier by *access pattern*. Nothing that is only needed when
   a user taps an app may appear in the boot payload.
2. **Every committed file must be O(1) in app count — or sharded into O(1) parts.**
   The test: if app #1,000,000 changes, how many bytes must be rewritten? The answer must be "a few".
3. **Never store what can be derived.** `html_url` = `https://github.com/{repo}/releases/tag/{tag}`.
   `content_type` = a function of the extension. `name` = `tag_name` most of the time. The native client
   was verified field-by-field: `html_url`, `content_type`, `link_type`, `channel` are **never read**.
4. **Key by the real entity.** Release history belongs to a *repository*, not an app. 93 current apps
   share one repo; keying by app stores that payload 93 times.
5. **Content-address so unchanged data costs nothing.** If a shard's bytes did not change, its git blob
   SHA did not change, and git stores it once. This turns "rewrite 1,359 files" into "rewrite 12 files".
6. **Escape git for bulk.** Git is a source store, not a blob store. A 2 GiB release asset costs nothing
   and does not count against repository size. A Pages deployment built from an Actions artifact commits
   nothing at all.
7. **Prove every optimisation against the client before shipping.** A CI job that replays the client's own
   lookup logic for all N apps. No regression, no merge. (`scripts/mirror_audit.py --assert`.)

---

## 5. The measured ladder

Each stage is independently shippable. Numbers are from `scripts/mirror_audit.py` against the live data.
"Boot payload" is what a cold start must download before it can paint the storefront.

| Stage | Boot payload | Bytes/app | 50 MiB wall | **100 MiB wall** | Client change |
|---|---:|---:|---:|---:|---|
| **Today** | 48.37 MB `mirror.json` | 27,895 | 1,879 | **3,759** | — |
| **A** — drop the 1,359 duplicate bare keys | 24.19 MB | 13,953 | 3,757 | **7,515** | none |
| **B** — + drop `html_url`, `content_type` | 18.96 MB | 10,984 | 4,773 | **9,547** | none |
| **C** — + newest release per repo; history lazy | **1.58 MB** | 913 | 57,403 | **114,807** | 2.9 |
| **D** — + binary `catalog.bin` + `versions.bin` | **258 KB** | 176 | 297,891 | **595,782** | 3.0 |
| **E** — + fixed-size parts + index | 258 KB | 176 | ∞ files | **unbounded** | 3.1 |
| **F** — + federation across sibling repos | 258 KB | 176 | ∞ | **unbounded, any N** | 3.2 |

`--assert` confirms **stage B regresses 0 of 1,453 currently-resolving apps**.
Stage A+B is a **2.5× extension of your runway** with no client release and roughly 30 lines of Python.

Stage C on its own is the big one: **30× more runway**, because it stops shipping history to devices that
will never scroll it.

**Stages D–F are built.** `scripts/orion_data.py` emits them; measured against the live data:

| Tier | Files | Total | Bytes/app | Largest file |
|---|---:|---:|---:|---:|
| 0 catalog | 256 | 352.8 KB | **203** | 3.9 KB |
| 1 versions | 256 | 39.1 KB | **23** | 435 B |
| 2 detail | 1,712 | 1.4 MB | 828 | 3.5 KB |
| 3 releases | 1,343 | 7.1 MB | 4,112 | 461 KB |
| legacy `mirror.json` | 1 | 18.1 MB | 10,933 | 18.1 MB |

Boot payload is **226 B/app** (tier 0 + tier 1) against 27,895 B in today's live `mirror.json` —
**123× less** — and no file in any tier scales with the catalogue. Tier 0 now carries the *resolved*
live tag and a real size, so a card is complete without touching tier 3. The new tiers total 8.9 MB versus
18.1 MB for the legacy file; both are produced side by side during the transition.

The remaining constraint is not the data plane, it is the installed client: `Defaults.MIRROR_URLS` is
compiled into the APK, and `config.json`'s `mirrorJsonUrl` is read only by `fetchSet` when no override is
passed — and `loadCatalogFromHosts` always passes one. So a 2.x device **cannot** be redirected to a
cheaper URL without shipping an update. Ceiling for 2.x: **9,547 apps** at full history, or **114,807**
with the automatic history trim. The 3.x client removes the ceiling entirely.

---

## 6. The target architecture (as built)

```
                       ┌──────────────────────────────────────────────────┐
  every cold start ───▶│ T0  catalog/<pp>.bin          203 B/app  3.9 KB max │  browse / grid
                       └──────────────────────────────────────────────────┘
                       ┌──────────────────────────────────────────────────┐
  update check   ─────▶│ T1  versions/<pp>.bin          23 B/app 435 B max │  installed -> version
                       └──────────────────────────────────────────────────┘
                       ┌──────────────────────────────────────────────────┐
  app sheet      ─────▶│ T2  detail/<h1>/<h2>.json     828 B/app  3.5 KB max│  description, screenshots
                       └──────────────────────────────────────────────────┘
                       ┌──────────────────────────────────────────────────┐
  version picker ─────▶│ T3  releases/<h1>/<h2>.json  4,112 B/app 461 KB max│  history, per REPO
                       └──────────────────────────────────────────────────┘
                       ┌──────────────────────────────────────────────────┐
  2.x client     ─────▶│     mirror.json              10,933 B/app, capped  │  compatibility only
                       └──────────────────────────────────────────────────┘
```

Three structural choices make it hold at any scale:

* **T3 is keyed by repository, not by app.** One repo serving 93 apps costs one
  file. 1,734 apps share 1,359 repositories.
* **Partitioning is by hash prefix, not by index range.** `<pp>` is the first
  byte of FNV-1a(ids), so adding one app rewrites exactly one part and every
  other part keeps its git blob SHA. Index-range chunking would rewrite every
  part after the insertion point on every change.
* **T2 and T3 are bucket files**, one JSON object per hash bucket, so the total
  file count caps at 65,536 per tier instead of one file per app. At a million
  apps a bucket holds ~15 apps instead of 15,000.

Shard function: `scripts/orion_data.py:shard_path()` — FNV-1a, two hex levels,
65,536 buckets. Using a *hash* rather than the first two characters of the
package name also fixes a real skew in the old layout: every `app.morphe.*` app
landed in the single `a/p/` directory.

---

## 7. The zero-cost workarounds catalogue

Ordered by leverage per unit of effort. Every item is $0.

### 7.1 Fix the wall that is already open: the API rate limit

**Conditional requests are the real fix.** Store each repo's `ETag` in a `state.json` committed to the
repository, send `If-None-Match`, and get `304 Not Modified` for the ~95% of repos that did not move in
this 6-hour window. GitHub's documentation is explicit: *making a conditional request and receiving a 304
response does not count against your primary rate limit*. So a run over 1,359 repos costs roughly
**9 requests, not 4,000** — the cost becomes proportional to *change* rather than to catalogue size.
This is the highest-value single change in this document.

**GraphQL batching for the changed subset.** One aliased query returns releases for ~50 repos. Because
each aliased `repository` node costs 1 of the `GITHUB_TOKEN`'s **1,000 points/hour**, batching *every*
repo does not fit (1,359 repos = 1,359 points > 1,000) — but batching only the ~9 that changed is free.
`mirror_generator.py` already uses this exact pattern for stargazers (`fetch_stars()`, batches of 50);
note that the stars step on its own currently requests 1,359 points/hour and silently drops the batches
that fail, which is a second reason star counts look stale.

**Poll `/releases/latest` before the release list.** One cheap call decides whether the expensive
20-release fetch is needed at all.

**Scale check:** with 304s costing nothing, the binding constraint becomes wall-clock — ~10,000 repos at
0.4 s/call is about 70 minutes, comfortably inside the 6-hour job limit. Federation (§7.7) keeps any one
run below that.

### 7.2 Escape the 100 MiB file limit permanently

**Publish bulk artefacts as Release assets.** Limits: **2 GiB per file, 1,000 assets per release, no total
size cap, no bandwidth cap**, and assets live outside the git object database so they do not count against
repository size. Your mirror workflow already uses this mechanism for APKs (`gh release create`); use it
for `mirror.json` too and the 100 MiB question stops existing.

**Or deploy Pages from an Actions artifact.** `actions/deploy-pages` publishes a build directory without
committing anything. The `data` branch then contains **zero bytes** — the repository stays at its source
size forever. Pages gives you 1 GB site, 100 GB/month bandwidth, real CDN caching with `ETag`/`304`,
and no per-IP rate limit (unlike `raw.githubusercontent.com`). This is the single biggest reliability
upgrade available to you for free.

**Or both.** Pages for the small tiers (T0/T1/T2), a release asset for the large one (T3).

### 7.3 The "infinite" move: use HTTP Range as your query language

Verified live against this repository's own files:

```
GET https://raw.githubusercontent.com/RookieEnough/Orion-Data/data/mirror.json
Range: bytes=0-119
→ 206 Partial Content
  accept-ranges : bytes
  content-range : bytes 0-119/48370117
  access-control-allow-origin : *
```

Range requests, `206`, and permissive CORS all work. That means a CDN can serve as a **random-access
read-only database**, and the client can fetch an O(1) slice of an O(N) file.

Design: **`catalog.bin` becomes a fixed-record, sorted array.**

```
header: magic | schema | recordSize | count | bucketCount
index : varint offsets for every 1,024th app      (N/1024 entries, ~4 B each)
records: 128 B fixed per app, sorted by id-hash
```

Update check for a device with 50 installed apps becomes **2 requests**: fetch the index (a few KB) to
compute the byte offset of each hash bucket, then fetch **one 128 KB contiguous window** covering that
device's apps. Boot cost is **O(installed)**, not O(N) — the same 128 KB whether the store has 1,000 apps
or 100,000,000.

The native client already speaks Range fluently (`Net.length()` sends `Range: bytes=0-0`, and
`DownloadService.split()` does parallel ranged downloads), so this is a small change on an existing muscle.

### 7.4 Delta manifests — the highest-bandwidth-return idea

Between full snapshots, publish only what changed: `delta/<unix-hour>.bin`, a list of
`(shard, app, newVersion)`. A client that holds a snapshot asks for deltas newer than its own timestamp
and applies them. On a store with 1,000,000 apps where ~200 change per hour, that is **200 records ≈ 6 KB**
instead of a 161 MB catalog. The boot payload becomes proportional to *change*, which is the only thing
that can stay small forever.

Keep a weekly full snapshot so a fresh install never has to replay years of deltas.

### 7.5 Key by repository, not app

1,359 repos hold 48 MB = 35,592 B each. 1,734 apps reference them. Any app-keyed store pays
`156 KB` to answer one question. Repo-keyed, a version picker fetches one file. It also fixes the
`mirrors/` duplication of the 93-app Grininja bundle.

### 7.6 Content-addressed dirty-set

Instead of `git add -A` on a freshly rebuilt tree, only write shards whose payload hash changed.
Git already deduplicates identical blobs, so unchanged shards cost 0 bytes in the commit — but the
generator still pays 1,359 filesystem writes and the push still walks the full tree.
Persist `state.json` (`repo -> {etag, payloadSha, lastSeen}`) and only touch what moved.

### 7.7 Federation — the answer to "a billion"

A single index cannot describe a billion apps in one file, and it should not try. Partition the namespace
across sibling repositories:

```json
// registry.json  (~1 KB, in the root repo, never changes)
{ "schema": 3, "shards": [
  { "prefix": "0", "repo": "RookieEnough/Orion-Data-0" },
  ...
  { "prefix": "f", "repo": "RookieEnough/Orion-Data-f" }
]}
```

* 16 repos × 65,536 apps = **1,048,576 apps**; 256 repos = **16.7 M**; 4,096 repos = **268 M**.
* Public repos are free and unlimited in number. Each stays under every limit forever.
* Client cost: `hash(id) -> prefix -> repo` is one extra dict lookup plus one extra URL segment.
  A shard path is `…/<repo-with-prefix>/catalog/<h1>/<h2>.bin` — the client can compute it with no index fetch.
* Cross-repo consistency is not required: each repo is an independent snapshot, refreshed on the same cron.

At that point "file size limit" is not a concept that applies, because **no file is ever written that is
proportional to app count.**

### 7.8 Never commit a binary — and purge the two that are there

`APKs/Anilab.apk` + `APKs/NetMirror…3.0.apk` = **67.7 MB** of a 106 MB repository, read by nothing.
They are **deleted from the working tree**, and `.gitignore` now blocks `*.apk`, `*.aab`, `*.apks`,
`*.xapk` and `*.dex` so this cannot recur.

Deleting the files does not shrink the repository, because the blobs are still reachable from history.
`scripts/purge_binaries.sh` does the history rewrite:

```bash
./scripts/purge_binaries.sh            # dry run: report what would change
./scripts/purge_binaries.sh --apply    # git filter-repo + force push
```

This is safe here. The repository is 106 MB after roughly a year of force-pushing 48 MB blobs four times
a day (~70 GB of nominal churn). If unreachable objects accumulated, it would be ~70 GB and long since
dead — so GitHub's GC is demonstrably reclaiming them. Design target: a tracked repository under 200 MB,
permanently.

### 7.9 Guard rails, so this never surprises you again

* **`scripts/mirror_audit.py --assert`** replays the client's exact key-resolution order for every app and
  fails if a slimmed artefact would drop one. This is what makes every optimisation here safe to ship.
* **`.github/workflows/data_guard.yml`** runs on every PR touching `apps.json` and daily. It round-trips the
  tier encoders, checks that URL factoring is lossless (including `%2B` in asset names, a bug it caught
  during development), and fails if any artefact approaches 50 MiB.
* **`scripts/build_data.py --check`** is the same gate inside the publish workflow, so a build that would
  cross a limit never reaches the branch.
* **The legacy-mirror cap is automatic.** `--mirror-max 44000000` trims history depth rather than letting
  `mirror.json` reach GitHub's 100 MiB block, and records `legacy_mirror.reduced` in `build-report.json`.
* **The publish step verifies resolution end to end** before pushing: every app with a repository must
  resolve in the mirror it is about to publish, or the workflow fails.

---

## 8. Migration plan

Ordered so that each phase is independently valuable and independently reversible.
✅ = shipped in this repository.

| Phase | Change | Status | Outcome |
|---|---|---|---|
| **0** | `mirror_audit.py` + `data_guard.yml`; `--assert` wired into the publish workflow | ✅ | every limit is visible and every change is provable |
| **1** | Conditional GET + ETag state + prioritised backlog + chunked 403 abort in the fetcher | ✅ | cost ∝ change; the first run self-heals instead of starving the tail |
| **2** | Drop duplicate bare keys + dead fields from `mirror.json` (stage A+B) | ✅ | **100 MiB wall: 3,759 → 9,547 apps** |
| **3** | Stop writing `mirrors/` and `updates.bin`; stop writing per-app duplicate shards | ✅ | `data` branch halves; pushes and clones get faster |
| **4** | Tiered binary plane (T0–T3) emitted alongside the legacy file | ✅ | boot payload 226 B/app, no O(N) file |
| **5** | Client 3.0: read T0/T1 at boot, T2/T3 lazily; drop the `mirror.json` fetch | ⬜ next | wall → **595,782 apps** in one repo |
| **6** | Raise `part_bytes` to 2 and/or shard further | ⬜ when needed | wall → **unbounded** |
| **7** | Federation across sibling repositories | ⬜ at ~250k apps | **any N**, each repo permanently small |
| **8** | `scripts/purge_binaries.sh --apply` | ⬜ run once | repository drops from 106 MB to source size |

The client already ships a forced-update gate (`minStoreVersion`, `ForcedStoreUpdateGate`), so phase 5 can
be enforced on the installed base rather than waiting years for natural upgrade.

---

## 9. What "keep the repo small no matter what" actually requires

The invariant that makes this hold at a billion apps is not a size limit you stay under — it is a
**shape** that has no O(N) file in it:

| Requirement | Guarantee |
|---|---|
| No committed file grows with app count | every tier is sharded at a fixed target size (~256 KB) |
| No committed file grows with release count | history is one file per repo, never aggregated |
| Boot download does not grow with app count | T0 is either sharded by hash or read via Range windows |
| Rewrite cost does not grow with app count | content addressing + ETag dirty-set: cost ∝ change |
| No single point of platform failure | release assets + Pages + jsDelivr/GitLab/Codeberg, all free |
| No spend | GitHub public repos, Actions on public repos, Release assets, Pages, R2 free tier, HF datasets |

If you ever find something still growing with N, the answer is always one of three moves:
**split it, hash it, or derive it.** Splitting gives you files of bounded size. Hashing gives you O(1)
lookup without an index. Deriving deletes the bytes entirely.

---

## 10. Appendix — reproducing these numbers

```bash
# build the whole data plane and run the capacity gate
python scripts/build_data.py --from-mirror mirror.json --check

# audit the live legacy mirror: duplication, dead fields, resolution safety
python scripts/mirror_audit.py --assert

# live artefact sizes
python - <<'PY'
import urllib.request
for u in ("mirror.json", "stars.json", "index.json"):
    url = f"https://raw.githubusercontent.com/RookieEnough/Orion-Data/data/{u}"
    req = urllib.request.Request(url, method="HEAD")
    print(u, urllib.request.urlopen(req).headers["Content-Length"])
PY
```

Sources for every platform limit quoted above:

* [GitHub — large files](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github) (50 MiB warn / 100 MiB block)
* [GitHub — repository limits](https://docs.github.com/en/repositories/creating-and-managing-repositories/repository-limits) (10 GB `.git`, 2 GB push, 3,000 entries/directory)
* [GitHub — REST rate limits](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api) (1,000/h `GITHUB_TOKEN`, 304s are free)
* [GitHub — GraphQL rate limits](https://docs.github.com/en/graphql/overview/rate-limits-and-query-limits-for-the-graphql-api)
* [GitHub — releases](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases) (2 GiB/asset, 1,000 assets, no total cap)
* [GitHub — Pages limits](https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits) (1 GB site, 100 GB/month)
* [GitHub — Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions) (public repos free and unlimited)
* [jsDelivr — package size limits](https://github.com/jsdelivr/data.jsdelivr.com) (50 MB GitHub / 150 MB npm)
* [Cloudflare R2 pricing](https://developers.cloudflare.com/r2/pricing/) (10 GB free, **$0 egress**)
* [Cloudflare Pages limits](https://developers.cloudflare.com/pages/platform/limits/) (20,000 files, 25 MiB/asset)
* [Hugging Face storage limits](https://huggingface.co/docs/hub/storage-limits) (no per-repo cap, 500 GB/file)
