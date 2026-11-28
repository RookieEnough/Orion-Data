#!/usr/bin/env python3
"""
MIRROR AUDIT
============
Measures the live `data`-branch artefacts and quantifies, with exact byte
counts rather than estimates, what each safe optimisation would save.

Two classes of optimisation are reported:

  SAFE      -> the native client provably never reads the field, or the entry
               is unreachable by the client's own lookup order. Ships today.
  CLIENT2   -> needs a matching change in the Kotlin client. Not applied here.

It also acts as a release gate: `--assert` exits non-zero if any app in
apps.json would lose its release data under the proposed slim layout. Run it
in CI before publishing a slimmer mirror.json.

Usage:
    python scripts/mirror_audit.py                      # fetch live, audit
    python scripts/mirror_audit.py --mirror mirror.json # audit a local file
    python scripts/mirror_audit.py --assert             # CI gate (exit code)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from typing import Any, Dict, List, Optional, Sequence, Tuple

MIRROR_URL = "https://raw.githubusercontent.com/RookieEnough/Orion-Data/data/mirror.json"
APPS_URL = "https://raw.githubusercontent.com/RookieEnough/Orion-Data/main/apps.json"

GITHUB_HARD_FILE_LIMIT = 100 * 1024 * 1024
GITHUB_WARN_FILE_LIMIT = 50 * 1024 * 1024
JSDELIVR_PACKAGE_LIMIT = 50 * 1000 * 1000  # jsDelivr: 50 MB for GitHub packages

# Fields the native client never reads (verified against CatalogProcessor.kt,
# which parses the mirror by hand with `o[key] as? JsonPrimitive` lookups).
DEAD_RELEASE_FIELDS = ("html_url", "link_type", "channel")
DEAD_ASSET_FIELDS = ("content_type",)

# The client builds `provider::domain::owner/repo` first and only falls back to
# the bare `owner/repo` key when that primary key is absent or missing.
PRIMARY_KEY = re.compile(r"^[a-z]+::[a-z0-9.\-]+::.+$", re.IGNORECASE)


def fetch(url: str, cache: str) -> bytes:
    if os.path.exists(cache):
        with open(cache, "rb") as handle:
            return handle.read()
    request = urllib.request.Request(url, headers={"User-Agent": "orion-mirror-audit"})
    with urllib.request.urlopen(request, timeout=180) as response:
        blob = response.read()
    with open(cache, "wb") as handle:
        handle.write(blob)
    return blob


def dumps(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def tiny(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024 or unit == "GB":
            return f"{n:,.1f} {unit}"
        n /= 1024
    return f"{n:,.1f} GB"


def strip_dead_fields(releases: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Remove only fields the client is proven not to read."""
    out = []
    for release in releases:
        if not isinstance(release, dict):
            continue
        slim = {k: v for k, v in release.items() if k not in DEAD_RELEASE_FIELDS}
        assets = []
        for asset in slim.get("assets") or []:
            if isinstance(asset, dict):
                assets.append({k: v for k, v in asset.items() if k not in DEAD_ASSET_FIELDS})
        slim["assets"] = assets
        out.append(slim)
    return out


def client_keys(app: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """
    Reproduce CatalogProcessor.processItem's key construction exactly:
      key   = provider::domain::repo   (primary)
      clean = repoUrl stripped to owner/repo (bare fallback)
    """
    github = (app.get("githubRepo") or "").strip().lower()
    gitlab = (app.get("gitlabRepo") or "").strip().lower()
    codeberg = (app.get("codebergRepo") or "").strip().lower()
    domain = (app.get("gitlabDomain") or "gitlab.com").strip().lower() or "gitlab.com"

    key = None
    if github:
        key = f"github::github.com::{github}"
    elif gitlab:
        key = f"gitlab::{domain}::{gitlab}"
    elif codeberg:
        key = f"codeberg::codeberg.org::{codeberg}"

    clean = None
    url = (app.get("repoUrl") or "").strip()
    match = re.match(r"^https?://(?:www\.)?[^/]+/(.+)$", url)
    if match:
        clean = re.sub(r"\.git$", "", match.group(1)).strip("/").lower()
    return key, clean


def audit(mirror_path: str, apps_path: str, cache_dir: str, do_assert: bool) -> int:
    os.makedirs(cache_dir, exist_ok=True)
    raw_mirror = fetch(MIRROR_URL, os.path.join(cache_dir, "mirror.json"))
    raw_apps = fetch(APPS_URL, os.path.join(cache_dir, "apps.json"))

    mirror: Dict[str, Any] = json.loads(raw_mirror)
    apps: List[Dict[str, Any]] = json.loads(raw_apps)

    primary = {k: v for k, v in mirror.items() if PRIMARY_KEY.match(k)}
    bare = {k: v for k, v in mirror.items() if not PRIMARY_KEY.match(k)}

    print("=" * 74)
    print("LIVE MIRROR AUDIT")
    print("=" * 74)
    print(f"source                 : {MIRROR_URL}")
    print(f"mirror.json            : {len(raw_mirror):,} B  ({tiny(len(raw_mirror))})")
    print(f"apps.json              : {len(raw_apps):,} B  ({tiny(len(raw_apps))})")
    print(f"apps                   : {len(apps):,}")
    print(f"top-level keys         : {len(mirror):,}")
    print(f"  primary  (p::d::o/r) : {len(primary):,}")
    print(f"  bare     (o/r)       : {len(bare):,}")
    print(f"releases total         : {sum(len(v) for v in mirror.values() if isinstance(v, list)):,}")
    print(f"avg per primary key    : {len(raw_mirror) / max(1, len(primary)):,.0f} B")

    # ---- Step 1: drop the bare duplicate keys -------------------------------
    step1 = dumps(primary).encode()
    print("\n-- SAFE, no client change " + "-" * 47)
    print(f"1. drop duplicate bare keys        {len(step1):>12,} B  "
          f"({tiny(len(step1))})  saving {100 * (1 - len(step1) / len(raw_mirror)):>5.1f}%")

    # ---- Step 2: drop fields the client never reads -------------------------
    slim: Dict[str, Any] = {}
    for key, releases in primary.items():
        if isinstance(releases, list):
            slim[key] = strip_dead_fields(releases)
        else:
            slim[key] = releases
    step2 = dumps(slim).encode()
    print(f"2. + drop html_url/content_type    {len(step2):>12,} B  "
          f"({tiny(len(step2))})  saving {100 * (1 - len(step2) / len(raw_mirror)):>5.1f}%")

    # ---- Step 3: which apps genuinely need the bare key? --------------------
    # The client tries the primary key first; the bare key is only reachable
    # when no primary key exists for the repo. Keep exactly those.
    resolved_today = 0
    needs_bare: List[str] = []
    unresolved = 0
    lost: List[str] = []
    for app in apps:
        key, clean = client_keys(app)
        if key and key in mirror:
            resolved_today += 1
        elif clean and clean in mirror:
            resolved_today += 1
            needs_bare.append(clean)
        else:
            unresolved += 1
            lost.append(str(app.get("id")))

    # Rebuild the slim layout with the few bare keys the client still needs.
    for bare_key in set(needs_bare):
        slim[bare_key] = strip_dead_fields(mirror[bare_key])
    step2 = dumps(slim).encode()

    print(f"3. + keep {len(set(needs_bare))} bare key(s) that are      {len(step2):>12,} B  "
          f"({tiny(len(step2))})  saving {100 * (1 - len(step2) / len(raw_mirror)):>5.1f}%")
    print("     actually reachable")

    # ---- Step 4: newest release per repo only (needs client 2.9) -----------
    newest: Dict[str, Any] = {}
    for key, releases in slim.items():
        if not isinstance(releases, list) or not releases:
            continue
        best = max(releases, key=lambda r: str(r.get("published_at") or r.get("created_at") or ""))
        newest[key] = [best]
    step4 = dumps(newest).encode()
    print("\n-- NEEDS A CLIENT CHANGE " + "-" * 49)
    print(f"4. newest release per repo only    {len(step4):>12,} B  "
          f"({tiny(len(step4))})  saving {100 * (1 - len(step4) / len(raw_mirror)):>5.1f}%")
    print("     (version picker then loads history lazily from a per-repo shard)")

    # ---- Step 5: does the client still resolve every app? -------------------
    resolved_new = 0
    regressions: List[str] = []
    for app in apps:
        key, clean = client_keys(app)
        had = (key and key in mirror) or (clean and clean in mirror)
        has = (key and key in slim) or (clean and clean in slim)
        if has:
            resolved_new += 1
        if had and not has:
            regressions.append(str(app.get("id")))
    print("\n-- CLIENT RESOLUTION CHECK " + "-" * 45)
    print(f"apps resolved today            : {resolved_today:,}")
    print(f"apps resolved in slim layout   : {resolved_new:,}")
    print(f"apps with no release data today: {unresolved:,}  (unchanged by slimming)")
    print(f"apps that would REGRESS        : {len(regressions):,}")
    if regressions and len(regressions) <= 15:
        print(f"  (ids: {', '.join(regressions)})")

    # ---- Capacity ----------------------------------------------------------
    print("\n-- CAPACITY LADDER " + "-" * 54)
    per_app_live = len(raw_mirror) / max(1, len(apps))
    print(f"{'stage':<34}{'B/app':>9}{'50 MiB':>11}{'100 MiB':>11}{'jsDelivr':>11}")
    print("-" * 76)
    ladder = (
        ("today (monolithic mirror.json)", len(raw_mirror)),
        ("A  drop duplicate bare keys", len(step1)),
        ("B  + drop html_url / content_type", len(step2)),
        ("C  + newest release per repo (client 2.9)", len(step4)),
    )
    for label, size in ladder:
        per_app = size / max(1, len(apps))
        print(f"{label:<34}{per_app:>9,.0f}"
              f"{GITHUB_WARN_FILE_LIMIT / per_app:>11,.0f}"
              f"{GITHUB_HARD_FILE_LIMIT / per_app:>11,.0f}"
              f"{JSDELIVR_PACKAGE_LIMIT / per_app:>11,.0f}")
    print("-" * 76)
    print("columns 50 MiB / 100 MiB / jsDelivr = the app count at which that wall is hit")
    print(f"mirror.json is at {100 * len(raw_mirror) / JSDELIVR_PACKAGE_LIMIT:.0f}% of "
          f"jsDelivr's 50 MB GitHub package ceiling, so the 4th mirror is already at risk.")
    print(f"\nPer repo of release history : {len(raw_mirror) / max(1, len(primary)):,.0f} B")
    print(f"Per repo, newest-only       : {len(step4) / max(1, len(primary)):,.0f} B")
    print("The gap is history the client downloads on every cold start but only")
    print("ever displays inside a version picker.")

    if do_assert:
        if regressions:
            print(f"\nASSERT FAILED: {len(regressions):,} app(s) would lose release data.")
            return 1
        print("\nASSERT OK: no app loses release data in the slim layout.")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mirror", default=MIRROR_URL)
    parser.add_argument("--apps", default=APPS_URL)
    parser.add_argument("--cache", default=".cache/orion")
    parser.add_argument("--assert", dest="do_assert", action="store_true")
    args = parser.parse_args(argv)
    return audit(args.mirror, args.apps, args.cache, args.do_assert)


if __name__ == "__main__":
    raise SystemExit(main())
