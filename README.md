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

**[INSTALLATION →](#installation)**

This project was created with [opencode](https://opencode.ai), an open-source
AI coding assistant.

> ## ⚠️ USE AT YOUR OWN RISK
>
> This tool **modifies the contents of your satnav SD card**. A wrong region,
> stream or version — or a botched update — can leave your navigation
> **non-functional** until the original card contents are restored (the unit
> only reads the card; it does not install anything from it). **You are solely
> responsible** for anything that happens to your car or SD card.
>
> Before you do anything:
> 1. **Back up the original SD card first** (the tool does this automatically
>    before every update — keep that backup).
> 2. Read the forum resource that documents the exact update procedure for
>    your model (**SEATCUPRA.NET — "Updating the inbuilt Mib2 Satnav"**, see
>    `MAPS.md`) and follow it.
> 3. Only use the correct map stream for your unit (MIB2 Standard /
>    DiscoverMedia2 / TomTom) and the correct region (`ECE` for Europe).
> 4. The tool is provided **without any warranty** and with **no guarantee
>    that it works on your specific unit**. The NDS format analysis is
>    reverse-engineered from public references, not from VW.
>
> There is **no official support** and no liability on our side if something
> goes wrong. If you are not comfortable with any of this, **do not use the
> SD-card updater** — the analysis/check features alone are harmless.

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
- **One-click car-profile setup** (web UI step 1, or `update_sd.py profile`):
  no hand-editing of config files — insert your SD card (or point at a
  backup of it) and the part number, region, original map release, card size,
  covered countries and the `OVERALL.NDS` workaround are detected and filled
  in automatically.
- **Web UI** (`mapui.py`): pick a package (folder, `.zip` or `.7z`), see the
  compatibility verdict, coverage and maps in the browser.

## Requirements

- Python 3.10+
- on Debian/Ubuntu: the `python3-venv` package (so `python3 -m venv` can
  create a pip-enabled virtualenv), e.g. `sudo apt install python3-venv`
- `cryptography`, `matplotlib`, `flask` (see `requirements.txt`)
- the `7z` binary on `PATH` for `.7z` packages (folders and `.zip` work
  without it; the tool auto-detects `7z`/`7zz`/`7za` and the standard
  Windows 7-Zip install locations)
- optional: Natural Earth 50m countries GeoJSON for the coverage map borders
- works on Linux, macOS and Windows (no Unix-only dependencies; `rsync` is
  used when available for fast tree copy/verify and falls back to a
  pure-Python copy otherwise)

## Installation

### One command

Two ways to get the tool running — both create the venv, install the
requirements and check for 7-Zip (`--ne` also fetches the Natural Earth
borders). They differ in **how the source code arrives**:

- **In an existing checkout** — for when you already have the repo (e.g. via
  `git clone` below). This keeps the `.git/` history, so you can later
  `git pull` the latest version:

  ```bash
  ./setup.sh              # Windows: setup.bat
  ```

- **Standalone, no git needed** — download the installer once, then run it
  (optionally into a specific folder). It pulls the repo archive from GitHub
  itself and sets everything up. You get a plain snapshot without `.git/`
  history (updates = download the installer again and re-run it in a fresh
  folder):

  ```bash
  curl -fsSL https://raw.githubusercontent.com/pennywiseNL81/mib2-satnav-tools/main/install.py -o install.py
  python3 install.py            # into the current directory
  python3 install.py ~/mib2-tools   # into a specific folder
  ```

Either way, **first time after installing** you create your car profile
(detected from your SD card or a backup — no config editing); see
[First run](#first-run-set-up-your-car-profile-no-config-editing) below.

### Or manually

```bash
git clone https://github.com/pennywiseNL81/mib2-satnav-tools.git && cd mib2-satnav-tools
python3 -m venv mib2nds-tool/.venv
mib2nds-tool/.venv/bin/pip install -r requirements.txt
```

**First time after installing**, set up your car profile — the tool detects
everything from your SD card (or a backup of it), no config editing needed:

```bash
mib2nds-tool/.venv/bin/python sd-updater/update_sd.py profile
```

Then start the UI or use the CLI:

```bash
# web UI -> http://127.0.0.1:5000
./start-mapui.sh        # Windows: start-mapui.bat

# CLI: compatibility check of a package
mib2nds-tool/.venv/bin/python mib2nds-tool/query.py --map DiscoverMedia2_EU-DL2_2710_V24.7z compat
```

On Windows the venv interpreter is `mib2nds-tool\.venv\Scripts\python.exe`
(the `.sh` launchers have `.bat` equivalents).

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
mib2nds-tool/.venv/bin/python mib2nds-tool/query.py --map <folder-or-zip> coverage --out _work/map.png

# SD-card updater
mib2nds-tool/.venv/bin/python sd-updater/update_sd.py detect
mib2nds-tool/.venv/bin/python sd-updater/update_sd.py list
mib2nds-tool/.venv/bin/python sd-updater/update_sd.py install --source <path>
mib2nds-tool/.venv/bin/python sd-updater/update_sd.py profile   # auto-setup car profile
```

All generated output goes to `_work/` (throwaway). Source packages are only
read, never modified.

## Configuration

The tool ships with a committed `config.json` **defaults template** (in git,
generic, no personal data) plus a gitignored `config.local.json` for *your*
car profile, created automatically by the
[First run](#first-run-set-up-your-car-profile-no-config-editing) flow. You
normally never edit config files by hand. Resolution order:

1. `MIB2_CONFIG` env var → an explicit personal-config path (handy when you
   want the profile outside the project).
2. `<project>/config.local.json` → the personal config created by first run.
3. `<project>/config.json` → the committed defaults template.

Your `config.local.json` looks like this:

```json
{
  "dirs": {
    "work": "_work",
    "downloads": "downloads",
    "backup": "BACKUP",
    "ne_geojson": "ne_50m.geojson"
  },
  "car": {
    "make": "SEAT",
    "nav_series": "MIB2 Standard (DiscoverMedia2)",
    "cartography": "TomTom",
    "part_number": "5QA919866H",
    "region_prefix": "ECE",
    "original_release": "0635 (ECE1 2016/17)",
    "card_size_gb": 16,
    "sd_card": "VAG MIB2 SD card (CID-bound)",
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
- Relative `dirs` values (work/downloads/backup) are resolved against the
  folder holding `MIB2_CONFIG` when set, else the project folder — so large
  data folders stay next to the config, outside git.

## First run: set up your car profile (no config editing)

You do **not** have to write `config.json` by hand. Both the web UI and the
CLI can detect your car from the SD card itself — or from a backup of it:

**In the web UI (easiest):**
1. Start the UI (`./start-mapui.sh`, then open http://127.0.0.1:5000).
2. In **step 1 "Car profile"** press **"Detect from SD card"** (card in the
   cardreader), pick a detected folder from the dropdown, or use
   **"Browse folders..."** to navigate to a backup folder containing `maps/`.
3. Check the detected values in the form and edit anything you like
   (e.g. trim `wanted_countries` to the countries you actually drive).
4. Press **"Save profile"**. The profile is written to `config.local.json`
   (a gitignored personal config; the committed `config.json` template stays
   untouched) and the tool picks it up immediately.

**From the CLI:**

```bash
# card in the cardreader:
mib2nds-tool/.venv/bin/python sd-updater/update_sd.py profile

# or from a backup folder (no card needed):
mib2nds-tool/.venv/bin/python sd-updater/update_sd.py profile --from /path/to/backup
```

What gets detected automatically:

| Field | Detected from |
|---|---|
| part number | `dbinfo.txt` — the most specific `PartNumberX` (last non-empty) |
| region prefix | first letters of `SystemName` (e.g. `ECE`) |
| original release | `ApplicationSoftwareVersionNumber` + `SystemName` |
| SD card size | measured from the mount (rounded to 8/16/32/64/128 GB) |
| wanted countries | the actual country coverage of the card/backup |
| OVERALL.NDS workaround | **enabled** when the card release is pre-2019 (`< 1520`); the original `maps/EEC/EEC_WLD/OVERALL.NDS` is copied into the backup dir so the updater can restore it |

Until you save a profile, the UI shows a warning banner in step 1 and the
CLI prints a "no car profile configured yet" note — a fresh install uses the
committed defaults template, so this stays visible until you set your car up.

### Guardrails

The profile setup and the SD updater refuse obviously wrong input so a
mistake cannot harm the card:

- The selected source must be a real MIB2 card/backup: it has to contain a
  `maps/` tree **and** `maps/00/nds/dbinfo.txt`, otherwise detection is
  refused with a clear error.
- Saving requires `part_number`, `original_release` and a valid
  `card_size_gb`; when the OVERALL.NDS workaround is enabled, an original
  `maps/EEC/EEC_WLD/OVERALL.NDS` must be available (detected or entered).
- Stored/used `OVERALL.NDS` files are sanity-checked for the `ZV-zlib`
  container magic; a wrong or corrupt file is refused both when saving the
  profile **and** before the workaround step writes to the card during
  install.
- `install` runs the compatibility check first (stream, region, version,
  coverage, size) and refuses "not suitable" packages; a free-space check
  aborts before `maps/` is cleared if the package would not fit.

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

## Testing

`tests/fixtures/mini_map/` is a tiny fake maps tree (no real NDS binaries)
used as a stand-in for a real package / SD card, and
`tests/fixtures/mini_card_0635/` is a tiny fake *old* card backup that
exercises the `profile` workaround path. The CI workflow
(`.github/workflows/test.yml`, Linux + Windows) compiles every module and
smoke-tests the CLI: the "no SD card" path of `update_sd.py detect`, the
compatibility check and the SD updater dry-run on the fixture, the car-profile
auto-setup (detect + save + OVERALL.NDS copy), and a web-UI start + shutdown
round trip.

```bash
# locally: compile + fixture checks (deps must be installed first)
python -m py_compile mib2nds-tool/*.py sd-updater/update_sd.py install.py
python mib2nds-tool/query.py --map tests/fixtures/mini_map compat
MIB2_CONFIG=/tmp/ci_config.json python sd-updater/update_sd.py install \
  --source tests/fixtures/mini_map --sd tests/fixtures/mini_map --dry-run --yes
```

## Disclaimer

> ## ⚠️ USE AT YOUR OWN RISK
>
> - Map packages are downloaded from the official VW map server. Check the
>   region/stream that matches your unit **before** installing anything.
> - The NDS format analysis is reverse-engineered from public references
>   (`pcbbc/NDS2SQLite`, `lprot/MIB-Tools`, `ratcashdev/zipvfs-converter`);
>   keys come from that work, not from VW.
> - SD-card updates **overwrite the card contents**. Always keep a backup
>   (the tool makes one automatically) and never use a generic SD card — only
>   the original VAG card is accepted (CID-bound).
> - **No warranty, express or implied. Use at your own risk.** If something
>   breaks, it is on you. The analysis, validation and coverage features are
>   read-only and safe; think twice (and read the forum thread) before using
>   the SD-card updater.

## License

`mib2-satnav-tools` is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by the
Free Software Foundation, either version 3 of the License, or (at your
option) any later version. See `LICENSE`.

Copyright (C) 2026 pennywiseNL81

**Legitimate use only.** This tool works only with map data you legitimately
hold — the SD card from your own VAG unit, or the official updates VW
publishes. It bundles **no map data**. You may analyse, back up, and update
maps for a unit you own. Map content remains the property of the VW Group
and its suppliers; do not redistribute or resell it.
