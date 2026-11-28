# Orion Data

**A structured data warehouse and distribution layer for the Orion Store ecosystem.**

Orion Data centralizes the metadata, configuration, and package artifacts that power the Orion Store. It is designed to be consumed programmatically by client applications and automated tooling, with a clear separation between configuration, build automation, and distributable assets. Every manifest is version-controlled, schema-consistent, and intended to be read directly at runtime.

## Table of Contents

1. [Overview](#overview)
2. [Repository Structure](#repository-structure)
3. [Directory Tree](#directory-tree)
4. [Core Manifests](#core-manifests)
5. [Automation & Workers](#automation--workers)
6. [Getting Started](#getting-started)
7. [Usage](#usage)
8. [Data Flow](#data-flow)
9. [Contributing](#contributing)
10. [License](#license)

## Overview

The repository serves as the **backend data layer** for the Orion Store. It answers three questions for any client:

- **What is available?** The application catalog defines every listing, its metadata, and version references.
- **Where is it hosted?** Mirror configuration defines primary and fallback sources for package retrieval.
- **What changed?** Structured release notes provide a consistent, machine-readable changelog.

By keeping these concerns in dedicated, version-controlled files, the store client remains a thin consumer while the data layer evolves independently.

## Repository Structure

| Path | Type | Purpose |
|------|------|---------|
| `.github` | Directory | GitHub Actions workflows and repository automation |
| `config` | Directory | Environment and application configuration files |
| `docs` | Directory | Documentation — **[DATA_CONTRACT.md](docs/DATA_CONTRACT.md)**, **[SCALING.md](docs/SCALING.md)** |
| `downloads` | Directory | Downloadable assets served to clients |
| `scripts` | Directory | Data-plane builder, auditors and utilities |
| `workers` | Directory | Background worker services for data processing |
| `apps.json` | Manifest | **Source of truth**: catalog of applications available in the store |
| `config.json` | Manifest | Primary runtime configuration |
| `mirror_config.json` | Manifest | Mirror endpoints and fallback source configuration |
| `package.json` | Manifest | Node.js project manifest and dependencies |
| `release_notes.json` | Manifest | Structured release history and changelogs |

Generated data is never committed to `main`. The builder writes it to `dist/`
and the `Build Data Plane` workflow publishes it to the `data` branch.

## Directory Tree

```text
Orion-Data/
├── .github/                  # GitHub Actions workflows and automation
│   └── workflows/            # build, guard and release pipelines
├── config/                   # Environment and application configuration
├── docs/                     # DATA_CONTRACT.md, SCALING.md, project docs
├── downloads/                # Downloadable assets served to clients
├── scripts/                  # Data-plane builder and auditors
├── workers/                  # Background worker services
├── apps.json                 # Source of truth: the application catalog
├── config.json               # Primary runtime configuration
├── mirror_config.json        # Mirror endpoints and fallback sources
├── package.json              # Node.js project manifest
├── release_notes.json        # Structured release history
└── README.md
```

## The Data Plane

`apps.json` is the only hand-maintained data file. `scripts/build_data.py`
compiles it, plus live release data from the upstream host APIs, into a
**tiered** data plane on the `data` branch — one tier per access pattern, so no
single file grows with the size of the catalogue:

| Tier | Path | Size | Read when |
|------|------|------|-----------|
| 0 | `catalog/<pp>.bin` | ~203 B/app | boot — grid and search |
| 1 | `versions/<pp>.bin` | ~23 B/app | background update check |
| 2 | `detail/<h1>/<h2>.json` | ~828 B/app | an app sheet opens |
| 3 | `releases/<h1>/<h2>.json` | ~4,112 B/app | the version picker opens |
| — | `mirror.json` | legacy | the shipped 2.x client only |

The wire format is specified in **[docs/DATA_CONTRACT.md](docs/DATA_CONTRACT.md)**.
The reasoning, the platform limits being defended against, and the path to
millions of apps are in **[docs/SCALING.md](docs/SCALING.md)**.

```bash
python scripts/build_data.py --from-mirror mirror.json --check   # build + gate
python scripts/mirror_audit.py --assert                          # audit live sizes
```

## Core Manifests

The heart of the repository is its set of JSON manifests. Each is the authoritative source for a specific concern and should be treated as a stable contract by consumers.

### `apps.json`

Defines the **application catalog** consumed by the Orion Store client. This is the authoritative source for app listings, metadata, and version references. Clients read this file to render the storefront and resolve which package to fetch.

### `mirror_config.json`

Specifies **mirror sources and fallback logic**. This allows clients to retrieve packages reliably even when a primary host is unavailable, improving resilience and download success rates across regions.

### `release_notes.json`

Holds **structured release notes**, enabling the store to surface changelogs to end users in a consistent, machine-readable format rather than free-form text.

### `config.json`

Provides **central configuration** for the repository's tooling and deployment behavior, keeping environment-specific values out of code.

### `package.json`

The **Node.js project manifest**, declaring dependencies and scripts used by the automation layer.

## Automation & Workers

The `.github` directory contains CI workflows that handle validation, artifact publishing, and release management. Supporting logic lives in two directories:

- **`scripts/`** - Utility and automation scripts invoked by workflows or run manually for maintenance tasks.
- **`workers/`** - Background worker services responsible for data processing, keeping generated manifests reproducible and up to date.

Together, these components ensure that data generation and distribution remain automated, auditable, and repeatable.

## Getting Started

```bash
git clone https://github.com/RookieEnough/Orion-Data.git
cd Orion-Data
npm install                     # only needed by the scraper/mirror tooling
```

`apps.json` is plain JSON and needs no build step. Building the data plane needs
Python 3.10+ and no third-party packages:

```bash
python scripts/build_data.py --from-mirror mirror.json --check
```

## Usage

The repository is intended to be **consumed programmatically**.

| Task | Read |
|------|------|
| Render the storefront grid | `catalog/<pp>.bin` |
| Check installed apps for updates | `versions/<pp>.bin` |
| Show description and screenshots | `detail/<h1>/<h2>.json` |
| List an app's versions and downloads | `releases/<h1>/<h2>.json` |
| Legacy 2.x client | `mirror.json` |
| Read runtime settings | `config.json` |

Full byte-level specification: **[docs/DATA_CONTRACT.md](docs/DATA_CONTRACT.md)**.

A minimal catalog lookup, using the reference decoder:

```python
import sys
sys.path.insert(0, "scripts")
import orion_data as od

base = "https://raw.githubusercontent.com/RookieEnough/Orion-Data/data/"
apps = {}                                     # id -> catalog row
for part in range(256):                       # or fetch only the parts you need
    path = f"catalog/{part:02x}.bin"
    try:
        apps.update({a["id"]: a for a in od.decode_catalog(open(path, "rb").read())})
    except FileNotFoundError:
        pass

app = apps["spotify-morphe"]
repo = app["repo"]                            # owner/repo
h1, h2 = od.shard_path(repo, 2)
releases = json.load(open(f"releases/{h1}/{h2}.json"))[repo]
newest = od.expand_compact_release(releases["r"][0], repo, releases["p"])
print(app["icon"], app["version"], newest["assets"][0]["browser_download_url"])
```

## Data Flow

1. **Source** — `apps.json` is hand-maintained.
2. **Fetch** — `scripts/build_data.py --fetch` refreshes release data with
   conditional GETs; unchanged repositories cost no API budget.
3. **Compile** — the four tiers are written to `dist/` deterministically, so
   unchanged shards keep their git blob SHA.
4. **Gate** — the capacity check fails the build before any artefact approaches
   a platform limit.
5. **Publish** — `.github/workflows/update_mirror.yml` force-pushes a single
   orphan snapshot to the `data` branch.
6. **Consume** — clients read the tier for the question they are asking.

Binary artefacts (APKs) belong in [Releases](https://github.com/RookieEnough/Orion-Data/releases),
never in git: a 2 GiB release asset costs nothing and does not count against
repository size.

## Contributing

Contributions are welcome. Please open an issue to discuss significant changes before submitting a pull request, and ensure any modifications to JSON manifests follow the existing schema and formatting conventions. Consistency in structure is what allows downstream clients to rely on this repository.

## License

This project is licensed under the GNU General Public License v3.0. See [LICENSE](LICENSE) for details.

---

*Orion Data is maintained as the distribution backbone for the Orion Store. For questions or feature requests, please open an issue.*
