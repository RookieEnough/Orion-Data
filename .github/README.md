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
| `APKs` | Directory | Hosted Android package (APK) artifacts |
| `config` | Directory | Environment and application configuration files |
| `docs` | Directory | Project documentation |
| `downloads` | Directory | Downloadable assets served to clients |
| `scripts` | Directory | Utility and automation scripts |
| `workers` | Directory | Background worker services for data processing |
| `apps.json` | Manifest | Catalog of applications available in the store |
| `config.json` | Manifest | Primary runtime configuration |
| `mirror_config.json` | Manifest | Mirror endpoints and fallback source configuration |
| `package.json` | Manifest | Node.js project manifest and dependencies |
| `release_notes.json` | Manifest | Structured release history and changelogs |

## Directory Tree

```text
Orion-Data/
├── .github/                  # GitHub Actions workflows and automation
│   └── workflows/            # CI/CD pipeline definitions
├── APKs/                     # Hosted Android package artifacts
├── config/                   # Environment and application configuration
├── docs/                     # Project documentation
├── downloads/                # Downloadable assets served to clients
├── scripts/                  # Utility and automation scripts
├── workers/                  # Background worker services
├── apps.json                 # Application catalog for the store
├── config.json               # Primary runtime configuration
├── mirror_config.json        # Mirror endpoints and fallback sources
├── package.json              # Node.js project manifest
├── release_notes.json        # Structured release history
└── README.md
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

To work with the repository locally, clone it and install dependencies:

```bash
git clone https://github.com/RookieEnough/Orion-Data.git
cd Orion-Data
npm install
```

No additional build step is required to read the manifests. They are plain JSON and can be parsed by any client or script.

## Usage

The repository is intended to be **consumed programmatically**. Clients and tooling should read the JSON manifests directly and resolve package downloads through the configured mirrors.

| Task | File to Read |
|------|--------------|
| Render the app catalog | `apps.json` |
| Resolve a download source | `mirror_config.json` |
| Display a changelog | `release_notes.json` |
| Read runtime settings | `config.json` |

A minimal example of loading the catalog:

```js
const apps = require('./apps.json');
const mirrors = require('./mirror_config.json');

// Resolve a package against the first available mirror
function resolveDownload(appId) {
  const app = apps.find((entry) => entry.id === appId);
  if (!app) throw new Error(`Unknown app: ${appId}`);
  return `${mirrors.primary}/${app.path}`;
}
```

## Data Flow

The lifecycle of a release follows a predictable path:

1. **Source** - Application metadata and artifacts are prepared.
2. **Processing** - Worker services in `workers/` transform raw input into structured manifests.
3. **Validation** - CI workflows in `.github/` verify schema and formatting.
4. **Publishing** - Artifacts are placed in `APKs/` and `downloads/`, and manifests are committed.
5. **Consumption** - Clients read the manifests and fetch packages via configured mirrors.

This separation keeps the client simple and the data layer authoritative.

## Contributing

Contributions are welcome. Please open an issue to discuss significant changes before submitting a pull request, and ensure any modifications to JSON manifests follow the existing schema and formatting conventions. Consistency in structure is what allows downstream clients to rely on this repository.

## License

Refer to the repository for licensing details.

---

*Orion Data is maintained as the distribution backbone for the Orion Store. For questions or feature requests, please open an issue.*
