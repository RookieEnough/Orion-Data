"""
orion_data — the tiered data plane for Orion Store.

Design contract
---------------
Every artefact this module writes obeys one rule:

    no single file grows with the number of apps.

That is achieved with four tiers, each answering exactly one question, and
with *hash-prefix partitioning* rather than index-order chunking so that
adding one app rewrites exactly one part (content addressing stays stable).

    Tier 0  catalog/<pp>.bin        browse / grid / search index
    Tier 1  versions/<pp>.bin       id -> latest version (update check)
    Tier 2  detail/<h1>/<h2>.json           description, screenshots, links
    Tier 3  releases/<h1>/<h2>.json         full release history, per REPO

`<pp>` is the first `part_bytes` bytes of the FNV-1a hash of the app id, hex.
The client computes a path with no index fetch, and a full browse is a fixed
number of GETs regardless of catalogue size.

`mirror.json` is still produced for the shipped 2.x client, in its legacy
shape, de-duplicated and stripped of fields the client provably never reads.
It is the only artefact with an O(apps) size, and `build()` bounds it
automatically (see BuildConfig.mirror_max_bytes).

Standard library only. Deterministic output: identical input produces
byte-identical files, which is what makes the dirty-set and git blob
deduplication work.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

SCHEMA = 3
MAGIC = b"ORD3"

# --------------------------------------------------------------------------
# Field sets. Keep these in sync with docs/DATA_CONTRACT.md.
# --------------------------------------------------------------------------

# Tier 0: everything needed to render a card and start an install.
CATALOG_COLUMNS = (
    "id", "name", "icon", "category", "author",
    "packageName", "repo", "version", "size", "platform",
    "downloadUrl", "releaseKeyword",
)

# Tier 2: only needed when the user opens an app.
DETAIL_FIELDS = ("description", "screenshots", "officialSite", "patches", "telegram")

PLATFORMS = ("Android", "Windows", "Linux", "iOS", "Web")

# The shipped 2.x client parses the mirror by hand and reads only these keys.
# `html_url`, `content_type`, `link_type` and `channel` are never touched.
LEGACY_DEAD_RELEASE_FIELDS = ("html_url", "link_type", "channel")
LEGACY_DEAD_ASSET_FIELDS = ("content_type",)


# --------------------------------------------------------------------------
# Primitive encoding
# --------------------------------------------------------------------------


def write_uvarint(out: bytearray, value: int) -> None:
    if value < 0:
        raise ValueError("uvarint cannot encode negative values")
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return


def read_uvarint(buf: bytes, pos: int) -> Tuple[int, int]:
    result = 0
    shift = 0
    while True:
        if pos >= len(buf):
            raise ValueError("truncated uvarint")
        byte = buf[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            return result, pos
        shift += 7


def write_bytes(out: bytearray, blob: bytes) -> None:
    write_uvarint(out, len(blob))
    out.extend(blob)


def read_bytes(buf: bytes, pos: int) -> Tuple[bytes, int]:
    length, pos = read_uvarint(buf, pos)
    if pos + length > len(buf):
        raise ValueError("truncated byte string")
    return buf[pos:pos + length], pos + length


def write_str(out: bytearray, text: str) -> None:
    write_bytes(out, text.encode("utf-8"))


def read_str(buf: bytes, pos: int) -> Tuple[str, int]:
    blob, pos = read_bytes(buf, pos)
    return blob.decode("utf-8"), pos


class StringTable:
    """Per-column interning. Ordered by first use, so output is deterministic."""

    __slots__ = ("_index", "_values")

    def __init__(self) -> None:
        self._index: Dict[str, int] = {}
        self._values: List[str] = []

    def intern(self, value: Any) -> int:
        text = "" if value is None else str(value)
        idx = self._index.get(text)
        if idx is None:
            idx = len(self._values)
            self._index[text] = idx
            self._values.append(text)
        return idx

    def __len__(self) -> int:
        return len(self._values)

    def write(self, out: bytearray) -> None:
        write_uvarint(out, len(self._values))
        for value in self._values:
            write_str(out, value)

    @staticmethod
    def read(buf: bytes, pos: int) -> Tuple[List[str], int]:
        count, pos = read_uvarint(buf, pos)
        values: List[str] = []
        for _ in range(count):
            text, pos = read_str(buf, pos)
            values.append(text)
        return values, pos


class HostTable:
    """
    Interns URL schemes+hosts. 8,470 screenshot URLs and 1,734 icon URLs share a
    handful of hosts, so the host is stored once and every URL becomes a
    (host_index, remainder) pair.
    """

    __slots__ = ("_index", "_values")

    def __init__(self) -> None:
        self._index: Dict[str, int] = {"": 0}
        self._values: List[str] = [""]

    @staticmethod
    def split(url: Optional[str]) -> Tuple[str, str]:
        if not url:
            return "", ""
        text = str(url)
        for scheme in ("https://", "http://"):
            if text.startswith(scheme):
                rest = text[len(scheme):]
                slash = rest.find("/")
                if slash == -1:
                    return scheme + rest, ""
                return scheme + rest[:slash], rest[slash:]
        return "", text

    def intern(self, url: Optional[str]) -> Tuple[int, str]:
        host, remainder = self.split(url)
        idx = self._index.get(host)
        if idx is None:
            idx = len(self._values)
            self._index[host] = idx
            self._values.append(host)
        return idx, remainder

    def resolve(self, idx: int, remainder: str) -> str:
        host = self._values[idx]
        return (host + remainder) if host else remainder

    def write(self, out: bytearray) -> None:
        write_uvarint(out, len(self._values))
        for value in self._values:
            write_str(out, value)


# --------------------------------------------------------------------------
# Keys, shards, partitions
# --------------------------------------------------------------------------


def fnv1a64(text: str) -> int:
    h = 0xCBF29CE484222325
    for byte in text.encode("utf-8"):
        h ^= byte
        h = (h * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return h


def part_of(key: str, part_bytes: int = 1) -> str:
    """Tier 0/1 partition name. 1 byte -> 256 parts, 2 bytes -> 65,536."""
    return fnv1a64(key).to_bytes(8, "little")[:part_bytes].hex()


def shard_path(key: str, depth: int = 2) -> Tuple[str, ...]:
    """Directory shards for tiers 2/3. Keeps every directory small."""
    digest = fnv1a64(key).to_bytes(8, "little")
    return tuple(f"{digest[i]:02x}" for i in range(depth))


def repo_key_of(app: Dict[str, Any]) -> str:
    """
    The real primary key of release data: owner/repo, lowercased.

    93 of the current apps resolve to one repository. Keying release history by
    repository instead of by app is what stops that payload being stored 93x.
    """
    for name in ("githubRepo", "gitlabRepo", "codebergRepo"):
        value = app.get(name)
        if value:
            return str(value).strip().strip("/").lower()
    url = str(app.get("repoUrl") or "")
    for host in ("github.com/", "gitlab.com/", "codeberg.org/"):
        if host in url:
            tail = url.split(host, 1)[1]
            pieces = tail.split("/")
            if len(pieces) >= 2:
                return f"{pieces[0]}/{pieces[1]}".replace(".git", "").rstrip("/").lower()
    return ""


def app_key_of(app: Dict[str, Any]) -> str:
    return str(app.get("id") or "").strip()


def provider_of(app: Dict[str, Any]) -> str:
    if app.get("githubRepo") or "github.com" in str(app.get("repoUrl") or ""):
        return "github"
    if app.get("gitlabRepo") or "gitlab" in str(app.get("repoUrl") or ""):
        return "gitlab"
    if app.get("codebergRepo") or "codeberg" in str(app.get("repoUrl") or ""):
        return "codeberg"
    return ""


def legacy_mirror_keys(app: Dict[str, Any]) -> List[str]:
    """
    The exact keys the shipped 2.x client will look up, in its order:

        "github::github.com::owner/repo"  ->  "owner/repo"  ->  bare repo
    """
    repo = repo_key_of(app)
    if not repo:
        return []
    provider = provider_of(app)
    keys: List[str] = []
    if provider == "github":
        keys.append(f"github::github.com::{repo}")
    elif provider == "gitlab":
        domain = str(app.get("gitlabDomain") or "gitlab.com").strip().lower() or "gitlab.com"
        keys.append(f"gitlab::{domain}::{repo}")
    elif provider == "codeberg":
        keys.append(f"codeberg::codeberg.org::{repo}")
    keys.append(repo)
    return keys


# --------------------------------------------------------------------------
# Tier 0 / Tier 1 encoding
# --------------------------------------------------------------------------


def encode_catalog(apps: Sequence[Dict[str, Any]], part_bytes: int = 1) -> Dict[str, bytes]:
    """Tier 0. One file per partition; each file is fully self-describing."""
    buckets: Dict[str, List[Dict[str, Any]]] = {}
    for app in apps:
        buckets.setdefault(part_of(app_key_of(app), part_bytes), []).append(app)

    files: Dict[str, bytes] = {}
    for part in sorted(buckets):
        rows_in = sorted(buckets[part], key=app_key_of)
        cols = {name: StringTable() for name in CATALOG_COLUMNS}
        hosts = HostTable()
        records: List[bytes] = []

        for app in rows_in:
            icon_idx, icon_rest = hosts.intern(app.get("icon"))
            record = bytearray()
            for name in CATALOG_COLUMNS:
                if name == "icon":
                    write_uvarint(record, icon_idx)
                    write_str(record, icon_rest)
                elif name == "platform":
                    platform = str(app.get("platform") or "Android")
                    write_uvarint(record, PLATFORMS.index(platform)
                                  if platform in PLATFORMS else 255)
                elif name == "repo":
                    # The derived owner/repo key, not an app field.
                    write_uvarint(record, cols["repo"].intern(repo_key_of(app)))
                else:
                    write_uvarint(record, cols[name].intern(app.get(name)))
            records.append(bytes(record))

        out = bytearray()
        out.extend(MAGIC)
        out.append(SCHEMA)
        write_uvarint(out, len(records))
        for name in CATALOG_COLUMNS:
            cols[name].write(out)
        hosts.write(out)
        for record in records:
            out.extend(record)
        files[part] = bytes(out)
    return files


def decode_catalog(blob: bytes) -> List[Dict[str, Any]]:
    if blob[:4] != MAGIC:
        raise ValueError("bad magic")
    pos = 4
    schema = blob[pos]
    pos += 1
    if schema != SCHEMA:
        raise ValueError(f"unsupported schema {schema}")
    count, pos = read_uvarint(blob, pos)
    cols: Dict[str, List[str]] = {}
    for name in CATALOG_COLUMNS:
        cols[name], pos = StringTable.read(blob, pos)
    host_count, pos = read_uvarint(blob, pos)
    hosts: List[str] = []
    for _ in range(host_count):
        text, pos = read_str(blob, pos)
        hosts.append(text)

    apps: List[Dict[str, Any]] = []
    for _ in range(count):
        app: Dict[str, Any] = {}
        for name in CATALOG_COLUMNS:
            idx, pos = read_uvarint(blob, pos)
            if name == "icon":
                remainder, pos = read_str(blob, pos)
                host = hosts[idx]
                app["icon"] = (host + remainder) if host else remainder
            elif name == "platform":
                app["platform"] = PLATFORMS[idx] if idx < len(PLATFORMS) else "Unknown"
            else:
                app[name] = cols[name][idx]
        apps.append(app)
    return apps


def encode_versions(apps: Sequence[Dict[str, Any]], part_bytes: int = 1) -> Dict[str, bytes]:
    """Tier 1. The whole update-check answer: id -> version."""
    buckets: Dict[str, List[Tuple[str, str]]] = {}
    for app in apps:
        key = app_key_of(app)
        buckets.setdefault(part_of(key, part_bytes), []).append(
            (key, str(app.get("version") or app.get("latestVersion") or "Latest")))

    files: Dict[str, bytes] = {}
    for part in sorted(buckets):
        ids = StringTable()
        versions = StringTable()
        rows = [(ids.intern(k), versions.intern(v))
                for k, v in sorted(buckets[part])]
        out = bytearray()
        out.extend(MAGIC)
        out.append(SCHEMA)
        write_uvarint(out, len(rows))
        ids.write(out)
        versions.write(out)
        for id_idx, ver_idx in rows:
            write_uvarint(out, id_idx)
            write_uvarint(out, ver_idx)
        files[part] = bytes(out)
    return files


def decode_versions(blob: bytes) -> Dict[str, str]:
    if blob[:4] != MAGIC:
        raise ValueError("bad magic")
    pos = 4
    schema = blob[pos]
    pos += 1
    if schema != SCHEMA:
        raise ValueError(f"unsupported schema {schema}")
    count, pos = read_uvarint(blob, pos)
    ids, pos = StringTable.read(blob, pos)
    versions, pos = StringTable.read(blob, pos)
    out: Dict[str, str] = {}
    for _ in range(count):
        id_idx, pos = read_uvarint(blob, pos)
        ver_idx, pos = read_uvarint(blob, pos)
        out[ids[id_idx]] = versions[ver_idx]
    return out


# --------------------------------------------------------------------------
# Releases
# --------------------------------------------------------------------------


def _epoch(value: Any) -> int:
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value or "")
    if not text:
        return 0
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S"):
        try:
            return int(time.mktime(time.strptime(text[:19], fmt)))
        except Exception:
            continue
    return 0


def _release_date(release: Dict[str, Any]) -> str:
    return str(release.get("published_at") or release.get("released_at")
               or release.get("created_at") or "")


def _release_sort(release: Dict[str, Any]) -> Tuple[int, str]:
    return (_epoch(_release_date(release)), str(release.get("tag_name") or ""))


def _assets_of(release: Dict[str, Any]) -> List[Dict[str, Any]]:
    assets = release.get("assets")
    if isinstance(assets, dict):          # GitLab
        assets = assets.get("links") or []
    out = []
    for asset in assets or []:
        if not isinstance(asset, dict):
            continue
        out.append({
            "name": str(asset.get("name") or ""),
            "size": int(asset.get("size") or 0),
            "url": str(asset.get("browser_download_url")
                       or asset.get("direct_asset_url") or asset.get("url") or ""),
            "downloads": int(asset.get("download_count") or 0),
        })
    return out


def canonical_asset_base(repo: str, tag: str, domain: str = "github.com") -> List[str]:
    """
    The URL prefixes a release's assets almost always live under. When the
    observed prefix equals one of these the client can rebuild it from
    (repo, tag) alone, so nothing needs to be stored at all.
    """
    repo = repo.strip("/")
    return [
        f"https://github.com/{repo}/releases/download/{tag}/",
        f"https://codeberg.org/{repo}/releases/download/{tag}/",
        f"https://{domain}/{repo}/-/releases/{tag}/downloads/",
        f"https://{domain}/{repo}/-/releases/{tag}/",
    ]


def repo_asset_prefix(releases: Sequence[Dict[str, Any]]) -> str:
    """
    The invariant URL prefix of a repository: every asset of every release
    shares it, minus the tag. `.../releases/download/` for GitHub.

    The tag is stripped from each release's own base *before* the common prefix
    is taken, otherwise a repo with a single release would report a prefix that
    already contains its tag and the client would rebuild the tag twice.
    """
    heads: List[str] = []
    for release in releases:
        tag = str(release.get("tag_name") or "")
        urls = [a["url"] for a in _assets_of(release) if a["url"]]
        if not urls:
            continue
        prefix = os.path.commonprefix(urls)
        cut = prefix.rfind("/")
        if cut < 0:
            continue
        base = prefix[:cut + 1]
        if tag and base.endswith(tag + "/"):
            base = base[: -(len(tag) + 1)]
        heads.append(base)
    if not heads:
        return ""
    shared = os.path.commonprefix(heads)
    cut = shared.rfind("/")
    return shared[:cut + 1] if cut >= 0 else ""


def compact_release(release: Dict[str, Any], repo: str = "",
                    repo_prefix: str = "") -> Dict[str, Any]:
    """
    Tier 3 shape. Short keys, epoch dates, no derivable fields.

    URL factoring in three steps:
      1. every asset of a release shares one prefix
         (`https://github.com/o/r/releases/download/v1.2.3/`);
      2. the invariant part of that prefix is hoisted to the repository record
         and stored once (`https://github.com/o/r/releases/download/`);
      3. the remainder is `tag + "/"`, so when it matches the release's own tag
         nothing is stored at all and the client rebuilds it.

    An asset whose URL is `prefix + name` therefore stores no URL at all, which
    is worth roughly 8x on the largest tier. Nothing here assumes a host's
    encoding rules -- it compares observed strings, it never constructs them.
    """
    tag = str(release.get("tag_name") or "")
    name = str(release.get("name") or "")
    assets = _assets_of(release)

    base = ""
    if assets:
        urls = [a["url"] for a in assets if a["url"]]
        if urls:
            prefix = os.path.commonprefix(urls)
            cut = prefix.rfind("/")
            base = prefix[:cut + 1] if cut >= 0 else ""

    # What expand() would rebuild for this release's base, with no stored `b`.
    expected = _rebuild_base(repo_prefix, tag, repo)

    if base and repo_prefix and base.startswith(repo_prefix):
        tail = base[len(repo_prefix):]
        stored_base = "" if tail == f"{tag}/" else tail
    else:
        stored_base = base

    # Only omit an asset URL when the client can unambiguously rebuild `base`.
    can_omit_base = bool(base) and base == expected

    packed = []
    for asset in assets:
        url = asset["url"]
        # `u` is either empty (fully derivable as base + name) or an absolute
        # URL. It is never a relative suffix, so the client needs no context to
        # interpret it. GitHub URL-encodes asset names (`+` becomes `%2B`), so
        # the common case is compared as strings, never rebuilt.
        if can_omit_base and url == base + asset["name"]:
            remainder = ""
        else:
            remainder = url
        packed.append({
            "n": asset["name"],
            "s": asset["size"],
            "u": remainder,
            "c": asset["downloads"],
        })

    return {
        "t": tag,
        "n": "" if name == tag else name,
        "p": 1 if release.get("prerelease") else 0,
        "d": _epoch(_release_date(release)),
        "b": stored_base,
        "a": packed,
    }


def _rebuild_base(repo_prefix: str, tag: str, repo: str = "") -> str:
    """The base URL expand() derives when a release stores no `b`."""
    if repo_prefix:
        return f"{repo_prefix}{tag}/"
    if repo:
        return canonical_asset_base(repo, tag)[0]
    return ""


def expand_compact_release(release: Dict[str, Any], repo: str = "",
                           repo_prefix: str = "") -> Dict[str, Any]:
    """Inverse of compact_release. Reference implementation for the client."""
    tag = str(release.get("t") or "")
    stored = release.get("b")
    if stored is None or stored == "":
        base = _rebuild_base(repo_prefix, tag, repo)
    elif stored.startswith("http"):
        base = stored
    else:
        base = (repo_prefix or "") + stored

    assets = []
    for asset in release.get("a") or []:
        if not isinstance(asset, dict):
            continue
        name = asset.get("n") or ""
        remainder = asset.get("u") or ""
        assets.append({
            "name": name,
            "size": int(asset.get("s") or 0),
            "browser_download_url": remainder if remainder else (base + name),
            "download_count": int(asset.get("c") or 0),
        })
    return {
        "tag_name": tag,
        "name": release.get("n") or tag,
        "prerelease": bool(release.get("p")),
        "published_at": int(release.get("d") or 0),
        "assets": assets,
    }


def legacy_release(release: Dict[str, Any]) -> Dict[str, Any]:
    """
    The shape the shipped 2.x client parses field-by-field, minus the fields it
    never reads. Keeping these long names is deliberate: it means the legacy
    mirror costs nothing in client compatibility.
    """
    return {
        "tag_name": str(release.get("tag_name") or ""),
        "name": str(release.get("name") or ""),
        "prerelease": bool(release.get("prerelease")),
        "published_at": _release_date(release),
        "assets": [{
            "name": a["name"],
            "size": a["size"],
            "browser_download_url": a["url"],
            "download_count": a["downloads"],
        } for a in _assets_of(release)],
    }


def latest_version(releases: Sequence[Dict[str, Any]]) -> str:
    if not releases:
        return ""
    best = max(releases, key=_release_sort)
    return str(best.get("tag_name") or "")


def human_size(total: float) -> str:
    if total <= 0:
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if total < 1024 or unit == "GB":
            return f"{total:.0f} {unit}" if unit == "B" else f"{total:.1f} {unit}"
        total /= 1024.0
    return ""


def apk_bytes(release: Dict[str, Any]) -> int:
    """Total size of the installable assets of one release."""
    total = 0
    for asset in _assets_of(release):
        name = asset["name"].lower()
        url = asset["url"].lower()
        if name.endswith(".apk") or ".apk" in url or "apk" in name:
            total += asset["size"]
    return total


def resolve_app(app: Dict[str, Any],
                releases_by_repo: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
    """
    Fill in the live version and a real size for one app.

    `apps.json` carries placeholders (`"Latest"`, `"Varies"`); the truth only
    exists in the release data. Resolving it here means a client can label and
    sort a card from tier 0 alone, with no tier-3 fetch on the hot path.
    """
    releases = releases_by_repo.get(repo_key_of(app)) or []
    if not releases:
        return app
    row = dict(app)
    version = latest_version(releases)
    if version:
        row["version"] = version
    newest = max(releases, key=_release_sort)
    size = human_size(apk_bytes(newest))
    if size:
        row["size"] = size
    return row


# --------------------------------------------------------------------------
# Build
# --------------------------------------------------------------------------


@dataclass
class BuildConfig:
    out_dir: str = "dist"
    # Tier 0/1 partitioning. 1 byte = 256 parts; 2 bytes = 65,536.
    part_bytes: int = 1
    # Directory depth for tiers 2/3.
    shard_depth: int = 2
    # How many releases per repo to keep in tiers 2/3 (0 = all).
    history_depth: int = 0
    # Hard ceiling for the legacy monolithic mirror. When the full-fidelity
    # file would exceed this, the depth is reduced until it fits, and the build
    # report records that the legacy client is running on reduced history.
    mirror_max_bytes: int = 45 * 1024 * 1024
    write_legacy_mirror: bool = True
    write_stars: bool = True
    compress_catalog: bool = False


@dataclass
class TierStat:
    name: str
    files: int = 0
    total: int = 0
    largest: int = 0
    largest_path: str = ""

    def add(self, size: int, path: str = "") -> None:
        self.files += 1
        self.total += size
        if size > self.largest:
            self.largest = size
            self.largest_path = path


@dataclass
class BuildReport:
    generated_at: str
    apps: int
    repos: int
    tiers: Dict[str, TierStat] = field(default_factory=dict)
    legacy_mirror: Dict[str, Any] = field(default_factory=dict)
    categories: Dict[str, int] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "schema": SCHEMA,
            "generated_at": self.generated_at,
            "apps": self.apps,
            "repos": self.repos,
            "categories": self.categories,
            "tiers": {k: {"files": v.files, "total_bytes": v.total,
                          "largest_bytes": v.largest, "largest_path": v.largest_path}
                      for k, v in self.tiers.items()},
            "legacy_mirror": self.legacy_mirror,
            "warnings": self.warnings,
            "notes": self.notes,
        }

    def print(self) -> None:
        print(f"\n{'tier':<26}{'files':>9}{'total':>14}{'B/app':>10}{'largest':>14}")
        print("-" * 74)
        for name, stat in self.tiers.items():
            per_app = stat.total / self.apps if self.apps else 0
            print(f"{name:<26}{stat.files:>9,}{_human(stat.total):>14}"
                  f"{per_app:>9.0f}B{_human(stat.largest):>14}")
        print("-" * 74)
        grand = sum(s.total for s in self.tiers.values())
        print(f"{'TOTAL':<26}{sum(s.files for s in self.tiers.values()):>9,}"
              f"{_human(grand):>14}")
        if self.legacy_mirror:
            info = self.legacy_mirror
            print(f"\nlegacy mirror.json: {info['bytes']:,} B "
                  f"({_human(info['bytes'])}), history depth {info['depth']}"
                  f"{'  [AUTO-REDUCED]' if info.get('reduced') else ''}")
        for warning in self.warnings:
            print(f"  ! {warning}")
        for note in self.notes:
            print(f"  . {note}")


def _human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024 or unit == "GB":
            return f"{int(n):,}B" if unit == "B" else f"{n:,.1f}{unit}"
        n /= 1024
    return f"{n:,.1f}GB"


def _write(path: str, blob: bytes) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(blob)
    return hashlib.sha256(blob).hexdigest()


def _dumps(value: Any) -> bytes:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False,
                      sort_keys=True).encode("utf-8")


def build(apps: Sequence[Dict[str, Any]],
          releases_by_repo: Dict[str, List[Dict[str, Any]]],
          config: BuildConfig,
          stars: Optional[Dict[str, Any]] = None,
          now: Optional[str] = None) -> BuildReport:
    """
    Write every tier. Returns a report; writes nothing outside config.out_dir.
    """
    out = config.out_dir
    os.makedirs(out, exist_ok=True)
    report = BuildReport(generated_at=now or time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                           time.gmtime()),
                         apps=len(apps),
                         repos=len({repo_key_of(a) for a in apps if repo_key_of(a)}))

    # Resolve the live version and size once, here, so every client gets them
    # for free instead of fetching tier 3 to label a card.
    resolved = [resolve_app(app, releases_by_repo) for app in apps]
    live = sum(1 for a, r in zip(apps, resolved) if a.get("version") != r.get("version"))
    if live:
        report.notes.append(
            f"{live:,} of {len(apps):,} apps had their placeholder version replaced "
            f"by the live tag from the release data")

    categories: Dict[str, int] = {}
    for app in apps:
        key = str(app.get("category") or "Other")
        categories[key] = categories.get(key, 0) + 1
    report.categories = dict(sorted(categories.items(), key=lambda kv: -kv[1]))

    # ---- Tier 0 ----------------------------------------------------------
    stat = TierStat("0 catalog/<pp>.bin")
    parts_meta = []
    for part, blob in encode_catalog(resolved, config.part_bytes).items():
        rel = f"catalog/{part}.bin"
        digest = _write(os.path.join(out, rel), blob)
        stat.add(len(blob), rel)
        parts_meta.append({"path": rel, "bytes": len(blob), "sha256": digest})
    report.tiers["0 catalog"] = stat

    # ---- Tier 1 ----------------------------------------------------------
    stat = TierStat("1 versions/<pp>.bin")
    for part, blob in encode_versions(resolved, config.part_bytes).items():
        rel = f"versions/{part}.bin"
        _write(os.path.join(out, rel), blob)
        stat.add(len(blob), rel)
    report.tiers["1 versions"] = stat

    # ---- Tier 2 ----------------------------------------------------------
    # Packed into hash buckets rather than one file per app, so the total file
    # count caps at 65,536 no matter how many apps exist. At today's scale that
    # is still roughly one file per app; at a million it is one file per 15.
    stat = TierStat("2 detail (lazy)")
    detail_buckets: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for app in apps:
        payload = {name: app[name] for name in DETAIL_FIELDS if app.get(name)}
        if not payload:
            continue
        detail_buckets.setdefault(shard_path(app_key_of(app), 2), {})[app_key_of(app)] = payload
    for bucket, payload in sorted(detail_buckets.items()):
        blob = _dumps(payload)
        rel = os.path.join("detail", bucket[0], f"{bucket[1]}.json")
        _write(os.path.join(out, rel), blob)
        stat.add(len(blob), rel)
    report.tiers["2 detail"] = stat

    # ---- Tier 3 ----------------------------------------------------------
    stat = TierStat("3 releases (lazy)")
    history: Dict[str, List[Dict[str, Any]]] = {}
    release_buckets: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for repo, releases in releases_by_repo.items():
        ordered = sorted(releases, key=_release_sort, reverse=True)
        if config.history_depth:
            ordered = ordered[:config.history_depth]
        if not ordered:
            continue
        history[repo] = ordered
        prefix = repo_asset_prefix(ordered)
        release_buckets.setdefault(shard_path(repo, 2), {})[repo] = {
            "p": prefix,
            "r": [compact_release(r, repo, prefix) for r in ordered],
        }
    for bucket, payload in sorted(release_buckets.items()):
        blob = _dumps(payload)
        rel = os.path.join("releases", bucket[0], f"{bucket[1]}.json")
        _write(os.path.join(out, rel), blob)
        stat.add(len(blob), rel)
    report.tiers["3 releases"] = stat

    # ---- Legacy monolithic mirror ---------------------------------------
    if config.write_legacy_mirror:
        report.legacy_mirror = _write_legacy_mirror(apps, history, out, config, report)

    # ---- Stars -----------------------------------------------------------
    if config.write_stars and stars is not None:
        rel = "stars.json"
        _write(os.path.join(out, rel), _dumps(stars))
        report.tiers["stars"] = TierStat("stars", 1, len(_dumps(stars)))

    # ---- Index -----------------------------------------------------------
    index = {
        "schema": SCHEMA,
        "generated_at": report.generated_at,
        "apps": report.apps,
        "repos": report.repos,
        "categories": report.categories,
        "part_bytes": config.part_bytes,
        "parts": len(parts_meta),
        "shard_depth": config.shard_depth,
        "paths": {
            "catalog": "catalog/{part}.bin",
            "versions": "versions/{part}.bin",
            "detail": "detail/{h1}/{h2}.json",
            "releases": "releases/{h1}/{h2}.json",
            "legacy_mirror": "mirror.json",
            "stars": "stars.json",
        },
        "tiers": report.as_dict()["tiers"],
        "legacy_mirror": report.legacy_mirror,
    }
    _write(os.path.join(out, "index.json"),
           json.dumps(index, indent=1, sort_keys=True).encode("utf-8"))
    _write(os.path.join(out, "build-report.json"),
           json.dumps(report.as_dict(), indent=1, sort_keys=True).encode("utf-8"))
    return report


def _write_legacy_mirror(apps: Sequence[Dict[str, Any]],
                         history: Dict[str, List[Dict[str, Any]]],
                         out: str,
                         config: BuildConfig,
                         report: BuildReport) -> Dict[str, Any]:
    """
    Emit the file the shipped 2.x client fetches.

    Two savings that need no client change:
      * one key per repo, not two (the bare key is unreachable, because the
        client always builds a primary key first);
      * no `html_url` / `content_type` / `link_type` / `channel`.

    One saving that does need a client change, and is therefore opt-in via
    history depth: the number of releases per repo.
    """
    def render(depth: int) -> bytes:
        payload: Dict[str, List[Dict[str, Any]]] = {}
        for app in apps:
            keys = legacy_mirror_keys(app)
            if not keys:
                continue
            repo = repo_key_of(app)
            releases = history.get(repo) or []
            if not releases:
                continue
            limited = releases[:depth] if depth else releases
            entry = [legacy_release(r) for r in limited]
            # Write the primary key; keep the bare key only when the client
            # could not construct a primary one at all.
            payload[keys[0]] = entry
            if len(keys) > 1 and not keys[0].startswith(("github::", "gitlab::", "codeberg::")):
                payload[keys[1]] = entry
        return _dumps(payload)

    depth = config.history_depth
    blob = render(depth)
    reduced = False
    while len(blob) > config.mirror_max_bytes and (depth == 0 or depth > 1):
        depth = max(1, (depth // 2) if depth else 8)
        blob = render(depth)
        reduced = True
        if depth == 1:
            break

    rel = "mirror.json"
    digest = _write(os.path.join(out, rel), blob)
    report.tiers["legacy mirror"] = TierStat("legacy mirror.json", 1, len(blob), len(blob), rel)
    if reduced:
        report.warnings.append(
            f"legacy mirror.json exceeded {config.mirror_max_bytes:,} B and was reduced "
            f"to {depth} release(s) per repo; the version picker in the shipped 2.x "
            f"client will show fewer entries. Ship the tiered client to lift this.")
    return {"file": rel, "bytes": len(blob), "sha256": digest,
            "depth": depth, "reduced": reduced}


# --------------------------------------------------------------------------
# Source adapters
# --------------------------------------------------------------------------


def releases_from_legacy_mirror(mirror: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    """
    Bootstrap from an existing mirror.json, so the first run of the new
    pipeline needs no API calls at all.

    Long-form keys are the legacy shape; short-form keys are already tier-3.
    """
    out: Dict[str, List[Dict[str, Any]]] = {}
    for key, value in mirror.items():
        if not isinstance(value, list) or not value:
            continue
        repo = key.split("::")[-1].strip().lower() if "::" in key else key.strip().lower()
        if not repo:
            continue
        normalised = []
        for release in value:
            if not isinstance(release, dict):
                continue
            if "t" in release:                    # tier-3 short form
                normalised.append({
                    "tag_name": release.get("t"),
                    "name": release.get("n") or release.get("t"),
                    "prerelease": bool(release.get("p")),
                    "published_at": release.get("d"),
                    "assets": [{"name": a.get("n"), "size": a.get("s"),
                                "browser_download_url": a.get("u"),
                                "download_count": a.get("c")}
                               for a in release.get("a") or [] if isinstance(a, dict)],
                })
            else:
                normalised.append(release)
        out[repo] = normalised
    return out


def load_apps(path: str) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as handle:
        apps = json.load(handle)
    if not isinstance(apps, list):
        raise ValueError(f"{path} is not a JSON array")
    return apps


def load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)
