# mib2-satnav-tools

Tools for analysing and updating VW-group **MIB2 Standard** (DiscoverMedia2 /
TomTom) satnav map packages — the `.7z` / `.zip` archives from the official
VW map server. Given a package, the tool tells you whether it fits your car
(series, region, version, country coverage, unpacked size vs. your SD card),
extracts the country coverage per update region, optionally converts the NDS
name index so you can search places and render a coverage map, checks for
newer releases, and can run the SD-card update itself.

This project grew out of updating a 2016 MIB2 Standard car (original map
`0635`) to the current `2710` / ECE 2027 release. It is not affiliated with
Volkswagen, SEAT, Škoda or Audi.

## Features

- **Compatibility check per package** (`compat`): nav series (Standard vs.
  High/Plus/Pro), region (`SystemName` in `dbinfo.txt` vs. your configured
  `region_prefix`, default `ECE`), version vs. known releases (warns when a
  newer one exists), country coverage vs. your wanted countries, and
  unpacked size vs. your SD card size. Includes the full install checklist
  (with the optional `OVERALL.NDS` "pair the unit to the original release"
  workaround for early cars).
- **Instant country coverage**: only the tiny per-region `OVERALL.NDS`
  (16 KB) files are read/converted — no need to convert the multi-GB
  databases to see which countries a package covers.
- **NDS → SQLite conversion** (`nds2sqlite.py`): the region databases use
  AES-128-ECB (first 64 bytes of each zlib payload); the key is
  auto-detected from the known VAG key set. `ROOT.NDS` and
  `maps/EEC/PRODUCT.NDS` are unencrypted.
- **Place search + coverage maps** (`search` / `coverage`): converts
  `PRODUCT/PRODUCT.NDS` (name index) on demand and decodes the NDS morton
  codes to lat/lon.
- **Update checker**: `updates.json` lists known releases with their direct
  VW download URLs; the UI probes the official URL pattern to discover newer
  releases automatically.
- **SD-card updater** (`update_sd.py`): detect the card (by content), full
  backup, extract, copy, optional `OVERALL.NDS` workaround, checksum verify,
  backup rotation.
- **Web UI** (`mapui.py`): pick a package (folder, `.zip` or `.7z`), see the
  compatibility verdict, coverage and maps in the browser.

## Requirements

- Python 3.10+
- `cryptography`, `matplotlib`, `flask` (see `requirements.txt`)
- the `7z` binary on `PATH` for `.7z` packages (folders and `.zip` work
  without it)
- optional: Natural Earth 50m countries GeoJSON for the coverage map borders

## Quick start

```bash
git clone <this-repo> && cd mib2-satnav-tools
python3 -m venv mib2nds-tool/.venv
mib2nds-tool/.venv/bin/pip install -r requirements.txt

# (optional) copy the example config and edit it to your car:
cp config.example.json config.json

# web UI -> http://127.0.0.1:5000
./start-mapui.sh

# CLI: compatibility check of a package
mib2nds-tool/.venv/bin/python mib2nds-tool/query.py --map DiscoverMedia2_EU-DL2_2710_V24.7z compat
```

## CLI

```bash
# list regions and the countries they cover
mib2nds-tool/.venv/bin/python mib2nds-tool/query.py --map <folder-or-zip> countries

# compatibility check (uses the car profile from config.json)
mib2nds-tool/.venv/bin/python mib2nds-tool/query.py --map <folder-or-zip> compat
mib2nds-tool/.venv/bin/python mib2nds-tool/query.py --map <folder-or-zip> compat --wanted "NLD,DEU,BEL"

# search a place name (converts PRODUCT.NDS on demand)
mib2nds-tool/.venv/bin/python mib2nds-tool/query.py --map <folder-or-zip> search Nijmegen
mib2nds-tool/.venv/bin/python mib2nds-tool/query.py --map <folder-or-zip> search "Den Haag" --contains

# render a coverage map
mib2nds-tool/.venv/bin/python mib2nds-tool/query.py --map <folder-or-zip> coverage --out _work/kaart.png

# SD-card updater
mib2nds-tool/.venv/bin/python sd-updater/update_sd.py detect
mib2nds-tool/.venv/bin/python sd-updater/update_sd.py list
mib2nds-tool/.venv/bin/python sd-updater/update_sd.py install --source <path>
```

All generated output goes to `_work/` (throwaway). Source packages are only
read, never modified.

## Configuration

`config.json` is resolved from (first hit wins): the `MIB2_CONFIG` env var,
`config.json` one level above the repo, or `config.json` in the repo root.
It is **not** committed — copy `config.example.json` and fill in your car.

```json
{
  "dirs": {
    "work": "_work",
    "downloads": "downloads",
    "backup": "BACKUP",
    "ne_geojson": "/tmp/ne_50m.geojson"
  },
  "car": {
    "make": "SEAT",
    "nav_series": "MIB2 Standard (DiscoverMedia2)",
    "cartography": "TomTom",
    "part_number": "5QA919866H",
    "region_prefix": "ECE",
    "original_release": "0635 (ECE1 2016/17)",
    "card_size_gb": 16,
    "sd_card": "VAG MIB2-SD-kaart (CID-gebonden)",
    "wanted_countries": ["NL", "DE", "BE", "LU", "GB", "IE", "FR", "AT", "CH"],
    "workaround": {
      "enabled": true,
      "overall_backup": "BACKUP/original/maps/EEC/EEC_WLD/OVERALL.NDS"
    }
  }
}
```

- `wanted_countries` accepts 2- or 3-letter ISO codes (e.g. `NL` or `NLD`).
- `car.workaround.overall_backup` points at the *original* card's
  `maps/EEC/EEC_WLD/OVERALL.NDS`. When enabled, the install plan and the SD
  updater will restore that file after copying the new maps — required on
  units that shipped with a very old release (pre-2019).
- Relative `dirs` values are anchored to the repo root.

## Map sources

See `MAPS.md`: the official references (SEATCUPRA.NET thread,
mib-helper.com) and the direct VW download URL pattern
(`Update_<YY>_<YY>/DiscoverMedia2_<region>_<version>_V<build>.7z`).
`updates.json` keeps the registry of known releases; the UI's update checker
probes those URLs and discovers newer ones.

## How it works / format details

The `.NDS` files are SQLite databases wrapped in the ZipVFS container
format, optionally AES-128-ECB-encrypted on the first 64 bytes of each zlib
payload. `ndsgeo.py` decodes the NDS morton code in the name index
(`nameFtsTable`) to lat/lon. See `ANALYSE.md` and `mib2nds-tool/README.md`.

## Disclaimer

- Map packages are downloaded from the official VW map server. Check the
  region/stream that matches your unit before installing.
- The NDS format analysis is reverse-engineered from public references
  (`pcbbc/NDS2SQLite`, `lprot/MIB-Tools`, `ratcashdev/zipvfs-converter`);
  keys come from that work, not from VW.
- SD-card updates overwrite the card contents. Keep a backup (the tool makes
  one automatically). Only the original VAG SD card is accepted by the unit
  (CID-bound).
- No warranty; use at your own risk.
