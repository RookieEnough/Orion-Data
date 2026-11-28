#!/usr/bin/env python3
"""
Build the Orion Data plane.

    python scripts/build_data.py --from-mirror mirror.json   # bootstrap, no API
    python scripts/build_data.py --fetch                     # live, conditional GET
    python scripts/build_data.py --fetch --graphql           # live, batched
    python scripts/build_data.py --from-mirror ... --check   # CI gate

Outputs (see docs/DATA_CONTRACT.md):

    dist/index.json                 manifest of the data plane
    dist/catalog/<pp>.bin           tier 0 — browse / grid
    dist/versions/<pp>.bin          tier 1 — update check
    dist/detail/<h1>/<h2>/<id>.json tier 2 — app page
    dist/releases/<h1>/<h2>/<repo>.json  tier 3 — history, per repository
    dist/mirror.json                legacy shape for the shipped 2.x client
    dist/stars.json
    dist/build-report.json

Every file is written deterministically, so unchanged data produces unchanged
git blobs and costs nothing to commit.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import orion_data as od  # noqa: E402

USER_AGENT = "OrionData-Builder/3.0"
GITHUB_API = "https://api.github.com"

# Safety rails, in bytes. 100 MiB is a hard GitHub rejection.
WARN_FILE = 50 * 1024 * 1024
HARD_FILE = 100 * 1024 * 1024
REPO_SOFT = 1 * 1024 * 1024 * 1024


# --------------------------------------------------------------------------
# Upstream fetching
# --------------------------------------------------------------------------


def _request(url: str, headers: Dict[str, str], timeout: int = 30):
    req = urllib.request.Request(url, headers=headers)
    return urllib.request.urlopen(req, timeout=timeout)


def fetch_conditional(repos: Sequence[str],
                      cached: Dict[str, List[Dict[str, Any]]],
                      etags: Dict[str, str],
                      token: Optional[str],
                      per_page: int = 20,
                      workers: int = 6,
                      limit: int = 0,
                      budget: int = 900) -> Tuple[Dict[str, List[Dict[str, Any]]],
                                                  Dict[str, str], Dict[str, str]]:
    """
    One conditional GET per repository, ordered so the catalogue converges.

    A 304 costs no primary rate-limit budget, so a steady-state run is nearly
    free. The first run has no ETags at all and will exceed the 1,000/hour
    budget, so requests are prioritised: repositories with no stored ETag go
    first, and the run stops after `budget` requests. Each run therefore drains
    part of the cold backlog and finishes with a cheap steady state, instead of
    hammering the same first N repos forever.
    """
    headers = {"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    cold = [r for r in repos if r not in etags]
    warm = [r for r in repos if r in etags]
    ordered = cold + warm
    if limit:
        ordered = ordered[:limit]
    if budget and len(ordered) > budget:
        print(f"   budget: {budget} requests this run, {len(ordered) - budget} deferred")
        ordered = ordered[:budget]

    result = dict(cached)
    new_etags: Dict[str, str] = {}
    status: Dict[str, str] = {}
    aborted = 0

    def one(repo: str) -> Tuple[str, str, Optional[List[Dict[str, Any]]], str]:
        url = f"{GITHUB_API}/repos/{repo}/releases?per_page={per_page}"
        local = dict(headers)
        etag = etags.get(repo)
        if etag:
            local["If-None-Match"] = etag
        try:
            with _request(url, local) as response:
                body = json.loads(response.read() or b"[]")
                return repo, response.headers.get("ETag", ""), body, "200"
        except urllib.error.HTTPError as error:
            if error.code == 304:
                return repo, etag or "", None, "304"
            if error.code in (403, 429):
                return repo, etag or "", None, f"{error.code}"
            if error.code == 404:
                return repo, etag or "", None, "404"
            return repo, etag or "", None, f"HTTP{error.code}"
        except Exception as error:                      # noqa: BLE001
            return repo, etag or "", None, f"ERR:{type(error).__name__}"

    # Chunked so a rate-limit wall stops the run instead of producing 400 more
    # pointless 403s.
    chunk = max(8, workers * 4)
    for start in range(0, len(ordered), chunk):
        batch = ordered[start:start + chunk]
        blocked = 0
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for repo, etag, body, code in pool.map(one, batch):
                status[code] = status.get(code, 0) + 1
                if etag:
                    new_etags[repo] = etag
                elif code == "304" and repo in etags:
                    new_etags[repo] = etags[repo]
                if body is not None:
                    result[repo] = body
                if code in ("403", "429"):
                    blocked += 1
        if blocked and blocked >= max(4, len(batch) // 2):
            aborted = len(ordered) - (start + len(batch))
            print(f"   rate limited; deferring {aborted} repos to the next run")
            break

    print(f"   fetch: {status}")
    return result, new_etags, status


GRAPHQL_QUERY = """
query($owner:String!,$name:String!,$n:Int!){
  repository(owner:$owner,name:$name){
    releases(first:$n,orderBy:{field:CREATED_AT,direction:DESC}){
      nodes{
        tagName name isPrerelease publishedAt
        releaseAssets(first:50){nodes{name size downloadUrl downloadCount}}
      }
    }
  }
}
"""


def _graphql_one(session_headers: Dict[str, str], repo: str, depth: int) -> Optional[List[Dict[str, Any]]]:
    owner, _, name = repo.partition("/")
    if not owner or not name:
        return None
    payload = json.dumps({"query": GRAPHQL_QUERY,
                          "variables": {"owner": owner, "name": name, "n": depth}}).encode()
    req = urllib.request.Request(f"{GITHUB_API}/graphql", data=payload,
                                headers={**session_headers, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            data = json.loads(response.read() or b"{}")
    except Exception:                                     # noqa: BLE001
        return None
    node = ((data.get("data") or {}).get("repository") or {}).get("releases") or {}
    nodes = node.get("nodes") or []
    releases = []
    for item in nodes:
        assets = ((item.get("releaseAssets") or {}).get("nodes")) or []
        releases.append({
            "tag_name": item.get("tagName"),
            "name": item.get("name"),
            "prerelease": bool(item.get("isPrerelease")),
            "published_at": item.get("publishedAt"),
            "assets": [{"name": a.get("name"), "size": a.get("size"),
                        "browser_download_url": a.get("downloadUrl"),
                        "download_count": a.get("downloadCount")} for a in assets],
        })
    return releases


def fetch_graphql(repos: Sequence[str], token: str, depth: int = 20,
                  changed_only: Optional[Sequence[str]] = None,
                  workers: int = 4) -> Dict[str, List[Dict[str, Any]]]:
    """
    Batched release lookup for the subset that conditional GET reported as
    changed. GraphQL costs 1 point per aliased repository against a 1,000
    point/hour budget for GITHUB_TOKEN, so only ever call it for the diff.
    """
    headers = {"User-Agent": USER_AGENT, "Authorization": f"Bearer {token}"}
    targets = list(changed_only if changed_only is not None else repos)
    out: Dict[str, List[Dict[str, Any]]] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for repo, releases in zip(targets, pool.map(
                lambda r: _graphql_one(headers, r, depth), targets)):
            if releases is not None:
                out[repo] = releases
    return out


# --------------------------------------------------------------------------
# Stars
# --------------------------------------------------------------------------

STARS_QUERY = """
query($aliases:String!){
  %s
}
"""


def fetch_stars(repos: Sequence[str], token: str, previous: Dict[str, Any],
                budget: int = 900, cursor: int = 0,
                workers: int = 4) -> Tuple[Dict[str, Any], int]:
    """
    Refresh stargazer counts for a rotating slice of the catalogue.

    GraphQL costs 1 point per aliased `repository` against a 1,000 point/hour
    budget, so asking for every repo in one run would blow the limit and drop
    batches silently. Instead each run refreshes `budget` repos starting at a
    persisted cursor, so the whole catalogue is covered every few runs while
    every individual run stays legal.
    """
    headers = {"User-Agent": USER_AGENT, "Authorization": f"Bearer {token}",
               "Content-Type": "application/json"}
    ordered = list(repos)
    if not ordered:
        return dict(previous), 0
    count = min(budget, len(ordered))
    window = [ordered[(cursor + i) % len(ordered)] for i in range(count)]
    batches = [window[i:i + 50] for i in range(0, len(window), 50)]

    def run(batch: Sequence[str]) -> Dict[str, Any]:
        parts = []
        for n, repo in enumerate(batch):
            owner, _, name = repo.partition("/")
            if not owner or not name:
                continue
            parts.append(f'r{n}: repository(owner: {json.dumps(owner)}, '
                         f'name: {json.dumps(name)}) {{ stargazerCount }}')
        if not parts:
            return {}
        payload = json.dumps({"query": "{" + " ".join(parts) + "}"}).encode()
        req = urllib.request.Request(f"{GITHUB_API}/graphql", data=payload, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                data = json.loads(response.read() or b"{}")
        except Exception:                                  # noqa: BLE001
            return {}
        out_ = {}
        for n, repo in enumerate(batch):
            node = ((data.get("data") or {}).get(f"r{n}") or {})
            if node.get("stargazerCount") is not None:
                out_[repo.lower()] = node["stargazerCount"]
        return out_

    merged = dict(previous)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for result in pool.map(run, batches):
            merged.update(result)
    return merged, (cursor + count) % len(ordered)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def load_history(dist: str) -> Dict[str, List[Dict[str, Any]]]:
    """Reload tier 3 from the previous build so unchanged repos cost nothing."""
    root = os.path.join(dist, "releases")
    history: Dict[str, List[Dict[str, Any]]] = {}
    if not os.path.isdir(root):
        return history
    for base, _, files in os.walk(root):
        for name in files:
            if not name.endswith(".json"):
                continue
            try:
                with open(os.path.join(base, name), "r", encoding="utf-8") as handle:
                    bucket = json.load(handle)
            except Exception:                              # noqa: BLE001
                continue
            if not isinstance(bucket, dict):
                continue
            for repo, payload in bucket.items():
                if not isinstance(payload, dict):
                    continue
                prefix = payload.get("p") or ""
                expanded = [od.expand_compact_release(r, repo, prefix)
                            for r in payload.get("r") or [] if isinstance(r, dict)]
                if expanded:
                    history[repo.lower()] = expanded
    return history


def check_limits(dist: str) -> int:
    """CI gate: fail if any artefact approaches a platform limit."""
    problems = 0
    total = 0
    biggest = ("", 0)
    for base, _, files in os.walk(dist):
        for name in files:
            path = os.path.join(base, name)
            size = os.path.getsize(path)
            total += size
            if size > biggest[1]:
                biggest = (os.path.relpath(path, dist), size)
            if size > HARD_FILE:
                print(f"  FAIL {os.path.relpath(path, dist)}: {size:,} B > 100 MiB hard limit")
                problems += 1
            elif size > WARN_FILE:
                print(f"  WARN {os.path.relpath(path, dist)}: {size:,} B > 50 MiB")
    print(f"\nlargest file : {biggest[0]} ({biggest[1]:,} B)")
    print(f"dist total   : {total:,} B ({total / 1024 / 1024:.1f} MB)")
    if total > REPO_SOFT:
        print(f"  WARN dist exceeds the 1 GB repository recommendation; "
              f"move tier 3 to release assets (see docs/SCALING.md §7.2)")
    return 1 if problems else 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apps", default="apps.json")
    parser.add_argument("--out", default="dist")
    parser.add_argument("--from-mirror", metavar="PATH",
                        help="bootstrap release data from an existing mirror.json")
    parser.add_argument("--fetch", action="store_true", help="refresh from GitHub")
    parser.add_argument("--graphql", action="store_true", help="batch changed repos via GraphQL")
    parser.add_argument("--limit", type=int, default=0, help="only fetch N repos (testing)")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--history-depth", type=int, default=0,
                        help="releases to keep in tiers 2/3 (0 = all)")
    parser.add_argument("--part-bytes", type=int, default=1, choices=(1, 2),
                        help="tier 0/1 partitioning: 1 = 256 parts, 2 = 65,536")
    parser.add_argument("--mirror-max", type=int, default=45 * 1024 * 1024,
                        help="ceiling for the legacy mirror.json")
    parser.add_argument("--no-legacy-mirror", action="store_true")
    parser.add_argument("--check", action="store_true", help="run the capacity gate")
    parser.add_argument("--fetch-stars", action="store_true",
                        help="refresh a rotating slice of stargazer counts")
    parser.add_argument("--stars", default="", help="path to an existing stars.json to merge")
    args = parser.parse_args(argv)

    started = time.time()
    apps = od.load_apps(args.apps)
    repos = sorted({r for r in (od.repo_key_of(a) for a in apps) if r})
    print(f"apps: {len(apps):,}   repos: {len(repos):,}   target: {args.out}/")

    history: Dict[str, List[Dict[str, Any]]] = {}
    if args.from_mirror:
        print(f"bootstrap from {args.from_mirror}")
        history = od.releases_from_legacy_mirror(od.load_json(args.from_mirror))
        print(f"   {len(history):,} repos with release data")
    else:
        history = load_history(args.out)
        if history:
            print(f"reusing {len(history):,} repos from the previous build")

    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if args.fetch:
        if not token:
            print("! no GH_TOKEN: 60 requests/hour. Pass a token in CI.")
        state_path = os.path.join(args.out, ".state", "etags.json")
        etags: Dict[str, str] = {}
        if os.path.exists(state_path):
            try:
                etags = json.load(open(state_path, encoding="utf-8"))
            except Exception:                              # noqa: BLE001
                etags = {}
        print(f"conditional refresh of {len(repos):,} repos")
        history, new_etags, status = fetch_conditional(
            repos, history, etags, token, workers=args.workers, limit=args.limit)
        os.makedirs(os.path.dirname(state_path), exist_ok=True)
        with open(state_path, "w", encoding="utf-8") as handle:
            json.dump(new_etags, handle, sort_keys=True)
        changed = [r for r in repos if status.get("200") and r not in etags]
        if args.graphql and token and changed:
            print(f"graphql refresh for {len(changed):,} changed repos")
            history.update(fetch_graphql(repos, token, changed_only=changed))

    stars: Optional[Dict[str, Any]] = None
    stars_path = args.stars or os.path.join(args.out, "stars.json")
    if os.path.exists(stars_path):
        try:
            stars = od.load_json(stars_path)
        except Exception:                                  # noqa: BLE001
            stars = {}
    if args.fetch_stars:
        gh_repos = {od.repo_key_of(a) for a in apps if od.provider_of(a) == "github"}
        gh_only = [r for r in repos if r in gh_repos]
        if not token:
            print("! no GH_TOKEN: skipping stars (GraphQL needs auth)")
        else:
            state_dir = os.path.join(args.out, ".state")
            os.makedirs(state_dir, exist_ok=True)
            cursor_path = os.path.join(state_dir, "stars-cursor.json")
            cursor = 0
            if os.path.exists(cursor_path):
                try:
                    cursor = int(json.load(open(cursor_path, encoding="utf-8")).get("cursor", 0))
                except Exception:                          # noqa: BLE001
                    cursor = 0
            stars, cursor = fetch_stars(gh_only, token, stars or {}, cursor=cursor)
            with open(cursor_path, "w", encoding="utf-8") as handle:
                json.dump({"cursor": cursor}, handle)
            print(f"   stars: {len(stars):,} repos, next cursor {cursor}")

    config = od.BuildConfig(
        out_dir=args.out,
        part_bytes=args.part_bytes,
        history_depth=args.history_depth,
        mirror_max_bytes=args.mirror_max,
        write_legacy_mirror=not args.no_legacy_mirror,
    )
    report = od.build(apps, history, config, stars=stars)
    report.print()
    print(f"\nbuilt in {time.time() - started:.1f}s -> {args.out}/index.json")

    if args.check:
        print("\ncapacity gate")
        return check_limits(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
