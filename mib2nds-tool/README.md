# mib2nds-tool

Read/analyse toolkit for VW MIB2 (NDS) navigation map packages, with a local
web UI to check downloaded packages. All commands run through the venv from
the repo root:

```bash
mib2nds-tool/.venv/bin/python mib2nds-tool/<script>.py ...
```

See the repo-level `README.md` for the overview, requirements and config.

## mapui.py — web UI (easiest)

```bash
mib2nds-tool/.venv/bin/python mib2nds-tool/mapui.py          # http://127.0.0.1:5000
mib2nds-tool/.venv/bin/python mib2nds-tool/mapui.py --port 5001
```

Pick a map package (an extracted folder with `maps/`, or a `.zip`/`.7z`) and
the UI will:

1. **Compatibility check** right after selection: verdict (geschikt / niet
   geschikt) with per-check pass/warning/fail — nav series (MIB2 Standard via
   the `STD2`/`DiscoverMedia2` name), region (`SystemName` in `dbinfo.txt`
   vs. the configured `region_prefix`, default `ECE`), version (known
   releases; warns when a newer one exists), country coverage vs. the wanted
   countries (editable, default from `car.wanted_countries`), and unpacked
   size vs. the configured SD-card size. Plus the full install checklist
   (including the optional `OVERALL.NDS` workaround from `car.workaround`).
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

## query.py — CLI (search / countries / coverage / compat)

```bash
# any map package (folder or zip): --map before the subcommand
mib2nds-tool/.venv/bin/python mib2nds-tool/query.py --map <folder-or-zip> countries
mib2nds-tool/.venv/bin/python mib2nds-tool/query.py --map <folder-or-zip> search Nijmegen
mib2nds-tool/.venv/bin/python mib2nds-tool/query.py --map <folder-or-zip> search "Den Haag" --contains
mib2nds-tool/.venv/bin/python mib2nds-tool/query.py --map <folder-or-zip> coverage --out _work/kaart.png

# compatibility check (default wanted countries from config, or --wanted)
mib2nds-tool/.venv/bin/python mib2nds-tool/query.py --map <folder-or-zip> compat
mib2nds-tool/.venv/bin/python mib2nds-tool/query.py --map <folder-or-zip> compat --wanted "NLD,DEU,BEL"
```

`search` and `coverage` convert the package on request (into
`_work/nds_out_<name>/`).

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
  `dirs.ne_geojson` (config) or `/tmp/ne_50m.geojson`. Without it the map
  renders without country borders and region labels fall back to numeric.
- For a `.zip`/`.7z`, nothing is extracted into the project folder; the
  needed members are read directly from the archive (single conversion
  outputs land in `_work/`).
