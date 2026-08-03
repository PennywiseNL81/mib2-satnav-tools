# AGENTS.md — contributor guide

Tooling and notes for VW MIB2 satnav map archives (`.zip` / `.7z`):
validation, country-coverage extraction, an optional NDS→SQLite conversion
for place-name search / coverage maps, a compatibility check against a
configured car profile, a release/update checker, and an SD-card updater.

The UI and CLI text are in Dutch; public docs (README, this file) are in
English. The project is not a git repo here only because the tool lives in a
larger personal archive — the `mib2-satnav-tools/` folder *is* the git repo.

## Layout

```
mib2-satnav-tools/
├── AGENTS.md                 # this file
├── README.md                 # public docs (English)
├── MAPS.md                   # forum links + official VW download URL pattern
├── ANALYSE.md                # technical analysis of the NDS format
├── updates.json              # registry of known releases + download URLs
├── start-mapui.sh            # start the web UI (uses the venv if present)
├── stop-mapui.sh             # stop the web UI on port 5000
├── sd-updater/
│   └── update_sd.py          # SD-card updater (CLI + web tab)
├── mib2nds-tool/
│   ├── mapui.py              # web UI (map select, coverage, search, update-check, SD tab)
│   ├── mapdata.py            # shared logic: config, validation, coverage, compat, sources
│   ├── updates.py            # registry load, online probe, resumable download
│   ├── nds2sqlite.py         # NDS (zipvfs + AES) -> SQLite converter
│   ├── ndsgeo.py             # NDS morton code <-> lat/lon + region/country map
│   ├── query.py              # CLI: search | coverage | countries | compat
│   ├── templates/map.html    # UI page
│   └── README.md
└── requirements.txt
```

## Configuration

Everything user-specific lives in a `config.json` that is **not** committed:

- `MIB2_CONFIG` env var, or `config.json` next to the repo (i.e. one level up),
  or `config.json` inside the repo root — first hit wins.

See `config.example.json` for the full schema. Important keys:

- `dirs.work` / `dirs.downloads` / `dirs.backup` — output/cache and package
  locations (relative entries are anchored to the repo root).
- `car.*` — the reference-car profile used by the compatibility check:
  `make`, `nav_series`, `cartography`, `part_number`, `region_prefix`
  (default `ECE`), `original_release`, `card_size_gb` (default 16),
  `sd_card`, `wanted_countries` (2- or 3-letter ISO codes; the tool
  normalises them), and `workaround.{enabled,overall_backup}`.

The `workaround` step is the known "pair the unit to the original map
release" fix: after copying new `maps/`, replace
`maps/EEC/EEC_WLD/OVERALL.NDS` with the original one. It only shows up in
the install plan / SD updater when `car.workaround.enabled` is true and an
`overall_backup` path is set.

## Commands

The scripts import each other and run from the repo root (a venv in
`mib2nds-tool/.venv` is created from `requirements.txt` if present):

```bash
mib2nds-tool/.venv/bin/python mib2nds-tool/query.py --map <folder-or-zip> compat
mib2nds-tool/.venv/bin/python mib2nds-tool/query.py --map <folder-or-zip> countries
mib2nds-tool/.venv/bin/python sd-updater/update_sd.py detect|list|install
mib2nds-tool/.venv/bin/python mib2nds-tool/mapui.py        # -> http://127.0.0.1:5000
```

`query.py coverage` needs Natural Earth 50m country boundaries in a GeoJSON
file (`NE_COUNTRIES` env var, or `dirs.ne_geojson` in the config); without
it the map renders without borders and region labels fall back to numbers.

## Rules

- Never modify, stage, or rewrite large binary files (multi-GB map archives).
  Read/verify only.
- `_work/` is throwaway output (converted SQLite files, images, scratch
  scripts). It can be deleted and regenerated via `nds2sqlite.py tree` +
  `query.py`, or the UI cleanup tab. Never treat it as a source of truth.
- `downloads/` holds the map archives; downloads land there. `updates.json`
  is a hand-maintained registry — update it when a new release appears on
  the forum.
- Never auto-extract multi-GB archives into the project. If extraction is
  ever required, do it in a temp folder (e.g. `/tmp`) and remove the temp
  files afterwards.
- Personal profiles and map-archive copies are never committed; keep them in
  `config.json` and outside the repo.
