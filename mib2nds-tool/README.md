# mib2nds-tool

Read/analyse toolkit for VW MIB2 (NDS) navigation map packages, with a local
web UI to check downloaded packages. All commands run through the venv from
the repo root:

```bash
mib2nds-tool/.venv/bin/python mib2nds-tool/<script>.py ...
```

See the repo-level `README.md` for the overview, requirements and config.

> ## ⚠️ USE AT YOUR OWN RISK
>
> The SD-card updater **overwrites your satnav SD card**. Always back up the
> original card first (the tool does), use the correct stream/region for your
> unit, and follow the official forum procedure. **No warranty — anything
> that happens to your car or card is your own responsibility.**

## mapui.py — web UI (easiest)

```bash
mib2nds-tool/.venv/bin/python mib2nds-tool/mapui.py          # http://127.0.0.1:5000
mib2nds-tool/.venv/bin/python mib2nds-tool/mapui.py --port 5001
```

Pick a map package (an extracted folder with `maps/`, or a `.zip`/`.7z`) and
the UI will:

1. **Compatibility check** right after selection: verdict (suitable / not
   suitable / suitable with caveats) with per-check pass/warning/fail — nav
   series (MIB2 Standard via the `STD2`/`DiscoverMedia2` name), region
   (`SystemName` in `dbinfo.txt` vs. the configured `region_prefix`, default
   `ECE`), version (known releases; warns when a newer one exists), country
   coverage vs. the wanted countries (editable, default from
   `car.wanted_countries`), and unpacked size vs. the configured SD-card
   size. Plus the full install checklist (including the optional
   `OVERALL.NDS` workaround from `car.workaround`).
2. **Validates** the package directly (without converting anything large)
   and shows version/system/part number from `dbinfo.txt`.
3. **Shows country coverage per region** — only the small `OVERALL.NDS`
   (16 KB) per region is converted; the multi-GB databases are untouched.
   Handy to see at a glance whether the countries you need are on the map.
4. Has a **"check countries" box**: type e.g. `NLD, DEU, BEL` and see
   ✔/✘ per country.
5. On request (button), only `PRODUCT.NDS` (~100 MB) is converted — after
   that the **coverage map** can be rendered and **place names searched**.
   Results are clickable and show a marker on the map.

Generated files go to `_work/` (throwaway). Source folders are only read,
never modified. Conversion is cached: a converted package is immediately
available next time.

## Car profile (first run — no config editing)

Step 1 of the UI ("Car profile") auto-detects your car from the SD card or a
backup folder and writes your personal `config.local.json` for you (the
committed `config.json` template stays untouched): detect (SD card, or a
folder picked with the built-in browser — folders with a `maps/` tree are
marked), review the
form (per-field `?` tooltips explain what is detected vs. manually filled),
save. The CLI equivalent is `update_sd.py profile [--from <folder>]`.
Detection reads only `dbinfo.txt` + the tiny per-region `OVERALL.NDS` files
and never writes to the source. Guardrails: sources without a `maps/` tree
or `dbinfo.txt` are refused; saving requires the essential fields; an
enabled OVERALL.NDS workaround needs a valid (ZV-zlib) original
`OVERALL.NDS` — checked again before the updater ever writes to the card.

## query.py — CLI (search / countries / coverage / compat)

```bash
# any map package (folder or zip): --map before the subcommand
mib2nds-tool/.venv/bin/python mib2nds-tool/query.py --map <folder-or-zip> countries
mib2nds-tool/.venv/bin/python mib2nds-tool/query.py --map <folder-or-zip> search Nijmegen
mib2nds-tool/.venv/bin/python mib2nds-tool/query.py --map <folder-or-zip> search "Den Haag" --contains
mib2nds-tool/.venv/bin/python mib2nds-tool/query.py --map <folder-or-zip> coverage --out _work/map.png --osm

# compatibility check (default wanted countries from config, or --wanted)
mib2nds-tool/.venv/bin/python mib2nds-tool/query.py --map <folder-or-zip> compat
mib2nds-tool/.venv/bin/python mib2nds-tool/query.py --map <folder-or-zip> compat --wanted "NLD,DEU,BEL"
```

`search` and `coverage` convert the package on request (into
`_work/nds_out_<name>/`).

### Coverage map

`coverage` renders the real name-index density on top of country
boundaries (yellow = covered countries): see the example in the top-level
README (`docs/coverage_map.png`, a DL2 2710 / ECE 2027 package). With
`--osm` it draws an OpenStreetMap background (like the web UI's Leaflet
map) and saves a self-contained non-transparent PNG with the OSM
attribution. The boundaries come from `dirs.ne_geojson` or the
`NE_COUNTRIES` env var (installable with `install.py --ne`); without them
the map renders borderless and region labels fall back to numbers. The web
UI (step 4/5) uses the same renderer.

## nds2sqlite.py — NDS → SQLite converter (low level)

```bash
# single file
mib2nds-tool/.venv/bin/python mib2nds-tool/nds2sqlite.py convert <in.NDS> -o <out.sqlite>

# whole tree (all .NDS below a directory, incl. the multi-GB region databases)
mib2nds-tool/.venv/bin/python mib2nds-tool/nds2sqlite.py tree <input-dir> <output-dir> -j 4

# known AES keys
mib2nds-tool/.venv/bin/python mib2nds-tool/nds2sqlite.py keys --full
```

The key is auto-detected (AES-128-ECB on the first 64 bytes of each zlib
page). See the docstring in `nds2sqlite.py` and `ANALYSE.md` for the ZipVFS
format.

## mapdata.py / ndsgeo.py — shared library

Used by `mapui.py` and `query.py`: package detection/validation, fast
country check from `OVERALL.NDS`, region derivation (`updateRegionId` →
region map), NDS morton code ↔ lat/lon, Mercator coverage map. `mapdata.py`
also holds the config layer and the car-profile/install-plan logic.

## Notes

- Venv `mib2nds-tool/.venv` (from `requirements.txt`): `cryptography`,
  `matplotlib`, `flask`.
- `query.py coverage` needs the Natural Earth 50m countries GeoJSON at
  `dirs.ne_geojson` (config), the `NE_COUNTRIES` env var, or a file named
  `ne_50m.geojson` in the system temp dir. `install.py --ne` downloads it.
  Without it the map renders without country borders and region labels fall
  back to numeric.
- `osutil.py` is the OS-portability layer (command runner with progress
  parsers, tree copy/verify, SD-mount detection, 7-Zip lookup); it keeps the
  tool working on Windows and Linux.
- `stop_mapui.py` (and `stop-mapui.sh`/`stop-mapui.bat`) stops the UI server
  on 127.0.0.1:5000 via `POST /api/shutdown`.
- For a `.zip`/`.7z`, nothing is extracted into the project folder; the
  needed members are read directly from the archive (single conversion
  outputs land in `_work/`).
