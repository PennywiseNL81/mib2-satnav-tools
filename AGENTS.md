# AGENTS.md — contributor guide

Tooling and notes for VW MIB2 satnav map archives (`.zip` / `.7z`):
validation, country-coverage extraction, an optional NDS→SQLite conversion
for place-name search / coverage maps, a compatibility check against a
configured car profile, a release/update checker, and an SD-card updater.

The UI and CLI text are in English; public docs (README, this file) are in
English too. The project is not a git repo here only because the tool lives
in a larger personal archive — the `mib2-satnav-tools/` folder *is* the git
repo. Portability: the code runs on Linux and Windows (see `osutil.py`);
CI smoke-tests both via GitHub Actions.

> ## ⚠️ USE AT YOUR OWN RISK
>
> The SD-card updater overwrites the card contents. Always keep the original
> card backup, use the correct stream/region, and follow the official forum
> procedure (`MAPS.md`). No warranty; the user is fully responsible for the
> car and the SD card.

## Layout

```
mib2-satnav-tools/
├── AGENTS.md                 # this file
├── README.md                 # public docs (English)
├── MAPS.md                   # forum links + official VW download URL pattern
├── ANALYSE.md                # technical analysis of the NDS format
├── updates.json              # registry of known releases + download URLs
├── install.py                # one-command setup (venv + deps + 7z check)
├── setup.sh / setup.bat      # wrappers around install.py
├── start-mapui.sh / .bat     # start the web UI (uses the venv if present)
├── stop-mapui.sh / .bat      # stop the web UI on port 5000 (via stop_mapui.py)
├── sd-updater/
│   └── update_sd.py          # SD-card updater (CLI + web tab)
├── mib2nds-tool/
│   ├── mapui.py              # web UI (map select, coverage, search, update-check, SD tab)
│   ├── mapdata.py            # shared logic: config, validation, coverage, compat, sources
│   ├── osutil.py             # OS portability: cmd runner, copy/verify, SD mounts, 7-Zip lookup
│   ├── updates.py            # registry load, online probe, resumable download
│   ├── nds2sqlite.py         # NDS (zipvfs + AES) -> SQLite converter
│   ├── ndsgeo.py             # NDS morton code <-> lat/lon + region/country map
│   ├── query.py              # CLI: search | coverage | countries | compat
│   ├── stop_mapui.py         # POST /api/shutdown to the UI server
│   ├── templates/map.html    # UI page
│   └── README.md
├── tests/fixtures/mini_map/  # tiny fake maps tree for CI smoke tests
├── tests/fixtures/mini_card_0635/  # tiny fake "old" SD-card backup (workaround path)
├── .github/workflows/test.yml # Linux + Windows test matrix
└── requirements.txt
```

## Configuration

Everything user-specific lives in a `config.json` that is **not** committed:

- `MIB2_CONFIG` env var, or `config.json` next to the repo (i.e. one level up),
  or `config.json` inside the repo root — first hit wins.

See `config.example.json` for the full schema. Important keys:

- `dirs.work` / `dirs.downloads` / `dirs.backup` — output/cache and package
  locations. Relative entries are anchored to the workspace root (the repo's
  parent when writable, else the repo root), keeping them out of the git repo.
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
`mib2nds-tool/.venv` is created from `requirements.txt` if present). On
Windows the interpreter is `mib2nds-tool\.venv\Scripts\python.exe`:

```bash
mib2nds-tool/.venv/bin/python mib2nds-tool/query.py --map <folder-or-zip> compat
mib2nds-tool/.venv/bin/python mib2nds-tool/query.py --map <folder-or-zip> countries
mib2nds-tool/.venv/bin/python sd-updater/update_sd.py detect|list|install
mib2nds-tool/.venv/bin/python sd-updater/update_sd.py profile   # auto-create config.json from the SD card
mib2nds-tool/.venv/bin/python mib2nds-tool/mapui.py        # -> http://127.0.0.1:5000
```

The car profile can be auto-created instead of hand-editing `config.json`:
`update_sd.py profile` reads the SD card (or `--from <backup-folder>`),
derives the part number, region, original release, card size, covered
countries and the `OVERALL.NDS` workaround (enabled for pre-2019 releases)
and writes `config.json` (target: `MIB2_CONFIG` if set, else next to the
repo). The web UI (step 1, "Car profile") exposes the same flow with a
form: detect, edit, save. `mapdata.derive_profile()` / `save_profile()`
are the shared implementation and write nothing on their own.

`query.py coverage` needs Natural Earth 50m country boundaries in a GeoJSON
file (`NE_COUNTRIES` env var, or `dirs.ne_geojson` in the config); `install.py
--ne` downloads it. Without it the map renders without borders and region
labels fall back to numbers.

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
- Run the smoke tests after changes (`tests/fixtures/mini_map` + CI workflow
  in `.github/workflows/test.yml`): compile every module and exercise the
  fixture through `query.py compat` and `update_sd.py install --dry-run`.
- All user-facing strings (UI, CLI, docs) are English; keep them that way.

## Tests

`tests/fixtures/mini_map/` is a tiny fake maps tree (a valid `maps/00/nds/`
layout with empty `.NDS` files) that stands in for a real package or SD card
in CI. `update_sd.py detect` finds nothing without it (exit 1), so the "no
card" path and the "with fixture" path are both covered.
`tests/fixtures/mini_card_0635/` is a tiny fake *old* card backup (version
`0635`, part `6P0919866H`, an `EEC/EEC_WLD/OVERALL.NDS`) used to exercise
`update_sd.py profile`: it must detect the workaround and copy the original
OVERALL.NDS into the configured backup dir.
