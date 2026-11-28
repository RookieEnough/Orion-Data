# Orion Data Plane — client contract

Schema **3**. This document is the contract between `RookieEnough/Orion-Data`
(the data plane) and any client that reads it. The shipped 2.x client keeps
working against `mirror.json`; a 3.x client reads the tiers below and gets a
boot cost that does not grow with the size of the catalogue.

Base URL — any of these serve identical bytes:

```
https://raw.githubusercontent.com/RookieEnough/Orion-Data/data/
https://gitlab.com/Grininja/orion-data/-/raw/data/
https://codeberg.org/RookieZ/Orion-Data/raw/branch/data/
https://cdn.jsdelivr.net/gh/RookieEnough/Orion-Data@data/
```

---

## 1. The four tiers

| Tier | Path | Size per app | When the client needs it |
|---|---|---|---|
| 0 | `catalog/<pp>.bin` | **203 B** | at boot, to draw the grid |
| 1 | `versions/<pp>.bin` | **23 B** | background update check |
| 2 | `detail/<h1>/<h2>.json` | **828 B** | only when an app sheet opens |
| 3 | `releases/<h1>/<h2>.json` | **4,112 B** | only when the version picker opens |
| — | `mirror.json` | 10,933 B | the 2.x client only |
| — | `index.json` | ~2 KB | once, for counts and schema |
| — | `stars.json` | ~38 KB | at boot, optional |

Measured against 1,734 apps / 1,359 repositories. No tier file grows with the
number of apps: `<pp>` spans 256 partitions and `<h1>/<h2>` spans 65,536
buckets, so the largest file in any tier stays under ~500 KB at any scale.
The largest file produced by the current build is `releases/…` at 461 KB.

---

## 2. Addressing

All hashing is **FNV-1a, 64-bit, little-endian bytes**, over the UTF-8 key.

```text
fnv1a64(s):
    h = 0xCBF29CE484222325
    for b in utf8(s):  h = (h XOR b) * 0x100000001B3  (mod 2^64)
    return h

part(key)   = hex( fnv1a64(key).le_bytes()[0:part_bytes] )   # part_bytes = 1
shard(key)  = ( hex(b[0]), hex(b[1]) )                       # two levels
```

* **Tier 0 / 1** key = the app `id`.
* **Tier 1** only: use the app `id` too, so an installed app maps to exactly
  one partition file — a device with 50 installed apps fetches at most 50 small
  files, never the whole catalogue.
* **Tier 2** key = the app `id` → `detail/<h1>/<h2>.json`.
* **Tier 3** key = `owner/repo`, lowercased → `releases/<h1>/<h2>.json`.

Because the path is derivable, a lookup never needs an index fetch.

`index.json` may raise `part_bytes` from 1 to 2 when the catalogue exceeds
roughly 10 million apps. Always read `index.json.part_bytes` rather than
hardcoding 1.

---

## 3. `index.json`

```json
{
  "schema": 3,
  "generated_at": "2026-11-28T13:00:00Z",
  "apps": 1734,
  "repos": 1440,
  "part_bytes": 1,
  "parts": 256,
  "shard_depth": 2,
  "categories": { "Media": 412, "Tools": 208, "...": 0 },
  "paths": {
    "catalog": "catalog/{part}.bin",
    "versions": "versions/{part}.bin",
    "detail": "detail/{h1}/{h2}.json",
    "releases": "releases/{h1}/{h2}.json",
    "legacy_mirror": "mirror.json",
    "stars": "stars.json"
  }
}
```

`categories` lets a client render category chips and counts before downloading
a single tier file.

---

## 4. Tier 0 — `catalog/<pp>.bin`

Little-endian, no padding, unsigned LEB128 varints throughout.

```text
magic       4 bytes   "ORD3"
schema      1 byte    = 3
count       uvarint   number of records in THIS file
columns     12 x ( uvarint n, n x ( uvarint len, utf8 ) )     # string tables
hosts       uvarint n, n x ( uvarint len, utf8 )              # URL host table
records     count x record
```

`columns` appear in exactly this order, and each record writes one varint per
column in the same order:

| # | column | type | notes |
|---|---|---|---|
| 0 | `id` | varint → table | |
| 1 | `name` | varint → table | |
| 2 | `icon` | **varint host index + string** | two fields; see below |
| 3 | `category` | varint → table | |
| 4 | `author` | varint → table | |
| 5 | `packageName` | varint → table | |
| 6 | `repo` | varint → table | `owner/repo`, lowercase, may be `""` |
| 7 | `version` | varint → table | may be `"Latest"` / `"Varies"` |
| 8 | `size` | varint → table | human string, may be `"Varies"` |
| 9 | `platform` | varint | index into `["Android","Windows","Linux","iOS","Web"]`, `255` = unknown |
| 10 | `downloadUrl` | varint → table | `"#"` when the release tier should be used |
| 11 | `releaseKeyword` | varint → table | asset-name filter, may be `""` |

**Icon reconstruction.** The icon column stores `hostIndex` then the URL
remainder. `hosts[0]` is always `""`, meaning "the remainder is the whole
string". Otherwise `icon = hosts[hostIndex] + remainder`. This is what turns
1,734 icon URLs into a handful of stored hosts.

Every string is in that column's own table, so `version: "Latest"` costs one
byte per app and `category: "Media"` costs one byte per app.

A reference decoder that is exercised by CI lives in
`scripts/orion_data.py` (`decode_catalog`). A Kotlin sketch:

```kotlin
// UNTESTED SKETCH - the byte layout above and scripts/orion_data.py are the
// authority. Verify against the Python decoder before shipping.
fun decodeCatalog(bytes: ByteArray): List<CatalogApp> {
    var p = 0
    require(bytes.copyOfRange(0, 4).contentEquals("ORD3".toByteArray())) { "bad magic" }
    p = 4
    val schema = bytes[p++].toInt()
    require(schema == 3) { "unsupported schema $schema" }
    val count = readUvarint(bytes, p).also { p = it.second }.first
    val tables = CATALOG_COLUMNS.map { buildList { repeat(readUvarint(bytes, p).also{p=it.second}.first) { add(readUtf8(bytes, p).also{p=it.second}.first) } } }
    val hosts = buildList { repeat(readUvarint(bytes, p).also{p=it.second}.first) { add(readUtf8(bytes, p).also{p=it.second}.first) } }
    return List(count) {
        val row = mutableMapOf<String, String>()
        for (i in CATALOG_COLUMNS.indices) {
            val idx = readUvarint(bytes, p).also { p = it.second }.first.toInt()
            if (i == 2) {                                    // icon
                val rest = readUtf8(bytes, p).also { p = it.second }.first
                row["icon"] = if (hosts[idx].isEmpty()) rest else hosts[idx] + rest
            } else if (i == 9) {                             // platform
                row["platform"] = PLATFORMS.getOrNull(idx) ?: "Unknown"
            } else {
                row[CATALOG_COLUMNS[i]] = tables[i][idx]
            }
        }
        row
    }
}
```

---

## 5. Tier 1 — `versions/<pp>.bin`

```text
magic       4 bytes   "ORD3"
schema      1 byte    = 3
count       uvarint
ids         string table   (uvarint n, n x ( uvarint len, utf8 ))
versions    string table
records     count x ( uvarint idIndex, uvarint versionIndex )
```

An update check is: for each installed app, compute `part(id)`, fetch that
partition (HTTP-cached), look up `id`, compare. **Cost is O(installed apps)**,
not O(catalogue).

---

## 6. Tier 2 — `detail/<h1>/<h2>.json`

A bucket object keyed by app id:

```json
{
  "spotify-morphe": {
    "description": "…",
    "screenshots": ["https://…", "https://…"],
    "officialSite": "https://…",
    "patches": ["…"],
    "telegram": "https://t.me/…"
  }
}
```

Only present fields are written. This is where the 545 bytes of screenshots and
259 bytes of description live, so they never enter the boot payload.

---

## 7. Tier 3 — `releases/<h1>/<h2>.json`

A bucket keyed by `owner/repo`:

```json
{
  "t8rin/imagetoolbox": {
    "p": "https://github.com/T8RIN/ImageToolbox/releases/download/",
    "r": [
      {
        "t": "4.3.0-rc03",
        "n": "ImageToolbox 4.3.0-rc03",
        "p": 0,
        "d": 1764288000,
        "b": "",
        "a": [
          { "n": "image-toolbox-4.3.0-rc03-arm64-v8a.apk", "s": 41234567, "u": "", "c": 1203 }
        ]
      }
    ]
  }
}
```

| key | meaning |
|---|---|
| `p` (repo) | invariant asset URL prefix for this repository |
| `r` | releases, **newest first** |
| `t` | tag name |
| `n` | release name; `""` when it equals the tag |
| `p` (release) | `1` if prerelease |
| `d` | published time, **unix epoch seconds** (0 = unknown) |
| `b` | per-release base override: `""`, a suffix of `p`, or an absolute URL |
| `a` | assets |
| `a[].n` | file name |
| `a[].s` | size in bytes |
| `a[].u` | `""` when the URL is derivable, else an **absolute** URL |
| `a[].c` | download count |

**Asset URL reconstruction** — this is the only non-obvious rule:

```text
base =
    release.b == ""            -> repo.p + release.t + "/"
    release.b starts "http"    -> release.b
    otherwise                  -> repo.p + release.b

url = asset.u != "" ? asset.u : base + asset.n
```

`repo.p` is the per-repository prefix; if it is absent, the GitHub canonical
form `https://github.com/{repo}/releases/download/{tag}/` is used.

The rule is deliberately *string-compared*, never constructed, because hosts
URL-encode asset names inconsistently (`+` becomes `%2B` on GitHub). When the
observed URL cannot be derived it is stored in full, so reconstruction is
always lossless. CI verifies this against all 81,878 live asset URLs.

`scripts/orion_data.py::expand_compact_release` is the reference implementation.

---

## 8. `mirror.json` — the 2.x compatibility path

Unchanged shape, two changes:

* **one key per repository**, `"{provider}::{domain}::{owner/repo}"`, lowercased;
* `html_url`, `content_type`, `link_type`, `channel` are gone — the 2.x client
  never reads them.

The client's own fallbacks (`mirror[clean]`, `mirror[bareRepo]`) still work, but
the builder only writes those extra keys for the handful of apps that cannot
produce a primary key.

The file is capped at `--mirror-max` (44 MB in CI). When the catalogue outgrows
it, the builder **reduces history depth** rather than crossing GitHub's 100 MiB
blob limit, and records `legacy_mirror.reduced = true` in `build-report.json`.
A 3.x client is unaffected; only the 2.x version picker shows fewer entries.

---

## 9. Client flows

**Cold start (3.x)**

```text
1. GET index.json                       ~2 KB     -> schema, counts, categories
2. GET catalog/<pp>.bin for the parts you want to show
                                        203 B/app -> draw the grid
3. GET stars.json                       ~38 KB    -> optional, defer
4. (background) GET versions/<pp>.bin for installed apps only
```

The storefront can paint after 1–2 requests. Nothing blocks on `mirror.json`.

**Update check** — one partition fetch per installed app, then compare
`versions[id]` against the installed version name. 50 installed apps touch at
most 50 partition files, each ~100 KB at a million apps.

**App sheet** — `detail/<shard(id)>.json` for description and screenshots;
`releases/<shard(repo)>.json` for the version list and download URLs. Both are
bucket files with stable bytes, so a second visit is a `304` and costs nothing.

**Downloads** — always take `assets[].u` (absolute) or reconstruct as in §7.
`HTTP Range` is supported by every mirror host, so resumed downloads keep
working.

---

## 10. Capacity

At **226 B per app** of boot payload (catalog + versions):

| Apps | Catalog | Versions | Detail | Releases | One repo total |
|---:|---:|---:|---:|---:|---:|
| 1,734 | 353 KB | 39 KB | 1.4 MB | 7.1 MB | **8.9 MB** |
| 100,000 | 20 MB | 2.3 MB | 83 MB | 411 MB | **516 MB** |
| 1,000,000 | 203 MB | 23 MB | 828 MB | 4.1 GB | **5.2 GB** |

One repository is comfortable to about **250,000 apps** (≈1 GB). Beyond that,
federate: a `registry.json` in the root repo maps a hash prefix to a sibling
repository, and the client adds one path segment. 256 sibling repos hold **64
million apps**, each permanently small. See `docs/SCALING.md` §7.7.

To trade history for headroom, rebuild with `--history-depth 5`; tier 3 shrinks
roughly 5x and the newest five versions per app remain selectable.

---

## 11. Compatibility rules

1. `schema` is bumped only for a breaking wire change. A client that sees an
   unknown schema must fall back to `mirror.json`.
2. Fields may be **added** to JSON tiers at any time; ignore unknown keys.
3. A missing or unreadable tier file must degrade that one app, never the whole
   catalogue.
4. `legacy_mirror.depth` in `build-report.json` tells you whether the 2.x
   version picker is currently running on reduced history.
