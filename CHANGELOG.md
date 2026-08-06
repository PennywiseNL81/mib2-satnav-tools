# Changelog

All notable changes to this project are documented here.
Releases follow [Semantic Versioning](https://semver.org/) and are tagged
with a `v` prefix (`v0.1.0`); tags and notes are managed via the GitHub
Releases section.

## [Unreleased]

### Changed

- Standalone installs (`install.py`) now update in place: `is_checkout()` is
  detected by `.git/` instead of the presence of `mib2nds-tool/mapui.py`, so
  re-running the installer in the same folder pulls the latest snapshot and
  keeps `config.local.json`, `downloads/` and `BACKUP/`. Git checkouts are
  unaffected and still update via `git pull`.

### Docs

- README: new **Updating** section (update path per install option) and the
  standalone-install wording corrected ("same folder" instead of
  "fresh folder").
- Web UI: the footer shows how to update the tool, and the version in the
  header links to the GitHub Releases page.
- AGENTS.md: note on standalone in-place updates.

## [0.1.0] - 2026-08-06

First tagged release. The web UI moves from a numbered step-by-step layout
to a tab-based one, and the map -> SD-card update flow is streamlined.

### Added

- Four-tab UI: **Car profile / Update / Map / Maintenance** (replaces the
  numbered step cards).
- Car-profile form pre-fills from the saved config on startup; no need to
  re-detect or browse for a folder just to view/edit the profile.
- **"Install in the Update tab"** from the compatibility check pre-selects
  the currently loaded map in the SD-updater; a new **SD** button next to
  "View" in the update list does the same for local packages.
- SD-updater card shows the automated plan plus the in-car manual steps
  up-front.
- Automatic online status check on startup (the cached list renders first).
- Version string in the UI header and `query.py --version` /
  `update_sd.py --version`; this changelog.

### Changed

- Update-check buttons renamed and streamlined: **Check online status**
  (verifies the known VW download links) and **Find new releases** (probes
  the official URL pattern); "Local list only" removed.
- Map tab split into "Downloaded maps" (auto-scanned) and
  "Load from disk -- viewer only" (explicitly not offered to the SD-updater).
- Folder browser adapts to its context: pick a map folder or detect a
  profile.
- "Open" became **View** (switches to the Map tab with loading progress).
- Manual install steps reworded into logical order (PC eject, insert, power
  on, re-set POIs).

### Fixed

- "Find new releases" progress could exceed 100%: the canary/region probe
  accounting now keeps progress within the planned grid, and the displayed
  percentage is clamped to 100 (also in the download status).

### Docs

- AGENTS.md: GitHub-hygiene rule (develop locally, commit coherent units,
  push finished sets once the CI smoke tests are green).
