# ANALYSE.md — MIB2 map data v2510 EU1 (NDS)

Analysis of the map package `STD2_2510_EU1_202525` (map version **2510**, ECE1
2026): how to look up whether a place is on the map, and the country coverage
per update region. The same findings apply to the newer 2710 / ECE 2027
packages.

## 1. Structure of the NDS data

The map data lives under `maps/00/nds/` and `maps/EEC/`:

```
maps/00/nds/
├── ROOT.NDS                      # product catalogue (NOT encrypted)
├── dbinfo.txt                    # SystemName="ECE1 2026", ApplicationSoftwareVersionNumber="2510"
└── PRODUCT/
    ├── PRODUCT.NDS               # product database: building blocks, update regions, versions, FTS index
    ├── E1/   E2/   E3/   E12/    # regional building blocks (update regions 0, 6, 7, 3)
    │   ├── OVERALL.NDS           # region metadata (country codes per region)
    │   ├── NAME.NDS              # name building block (streets/roads, 2.36M records in E1)
    │   ├── BMD.NDS / ROUTING.NDS # base map display / routing
    │   ├── POI.NDS / FTS.NDS / SLI.NDS / SPEECH.NDS / TI.NDS / JV.NDS
maps/EEC/
├── PRODUCT.NDS                   # European product (NOT encrypted)
└── EEC_WLD/ (BMD.NDS, OVERALL.NDS)   # coarse "Europe + world" base
```

## 2. Country coverage (verified)

From `OVERALL.sqlite` per region (`regionMetadataTable.isoCountryCode`):

| update region | folder | countries |
|---|---|---|
| 0 | E1 | **PRT** (Portugal), **GIB** (Gibraltar), **ESP** (Spain) |
| 6 | E2 | **MCO** (Monaco), **FRA** (France), **AND** (Andorra) |
| 7 | E3 | **ISL** (Iceland), **IRL** (Ireland), **GBR** (United Kingdom) |
| 3 | E12 | **NLD** (Netherlands), **LUX** (Luxembourg), **BEL** (Belgium) |

The speech databases in `maps/00/sds/` (AD, BE, ES, FR, GB, GI, IE, IS, LU,
MC, NL, PT) match these 12 countries one-to-one. **Conclusion: the
Netherlands/Belgium/Luxembourg are in the EU1 map (region E12).**

## 3. File format: SQLite via ZipVFS + AES

Every `.NDS` file is a SQLite database wrapped in the ZipVFS container format:

| offset | field |
|---|---|
| 0 | `ZVZL` signature |
| 108 | `dataStart` (8 bytes, big-endian) |
| 140 | `dbSize` (8 bytes) |
| 172 | `pageSize` (= 65536) |
| 176 | version |
| 200 | page map: 8-byte entries per page `offset = e>>24`, `size = (e>>7)&0x1FFFF` |

Per page: 6-byte slot header (`pageno = u32>>1`, `pagelen = u32@2 & 0x1FFFF`),
then the payload at `offset+6` → **zlib** → 64 KiB SQLite page.

**Encryption**: AES-128-**ECB**, no padding, only the first 64 bytes of each
zlib payload (so the zlib stream itself stays readable). The key for this
package is `z463rTyK9YS3JIPq` and is found automatically by trying all 12
known VAG keys. `ROOT.NDS` and `maps/EEC/PRODUCT.NDS` are unencrypted.

References: `pcbbc/NDS2SQLite` (C#), `lprot/MIB-Tools` (Python),
`ratcashdev/zipvfs-converter` (issue #4: VAG MIB2 keys).

## 4. Coordinates: the NDS morton code

The full-text name index `nameFtsTable` (in `PRODUCT/PRODUCT.sqlite`) stores a
64-bit `mortonCode` per name. Decoding to latitude/longitude:

```
x = interleave(even bits of morton, LSB-first)   # 32-bit signed
y = interleave(odd bits, LSB-first)              # 31-bit, y>=2^30 → y-=2^31
lat = y * 360 / 2^32
lon = x * 360 / 2^32
```

(or simply `ndsgeo.morton_to_ll(morton)`). This is the official NDS definition
(`ndsmath::MortonCode`). Validation:

| place | decoded | actual | error |
|---|---|---|---|
| Madrid | 40.4167, -3.7003 | 40.4168, -3.7038 | < 0.004° |
| Amsterdam | 52.3732, 4.8907 | 52.3676, 4.9041 | < 0.014° |
| London | 51.5002, -0.1262 | 51.5074, -0.1278 | < 0.008° |
| Paris | 48.8569, 2.3508 | 48.8566, 2.3522 | < 0.002° |
| Rotterdam | 51.9229, 4.4706 | 51.9225, 4.4792 | < 0.009° |
| Nijmegen | 51.8417, 5.8586 | 51.8424, 5.8528 | < 0.006° |

## 5. The name index (`nameFtsTable`)

Columns: `namedObjectId`, `mortonCode`, `updateRegionId`,
`criterionA`..`criterionJ`. Usage: `criterionB` = place name, `criterionC` =
postcode, `criterionA` = region reference. Multiple rows per object (language
variants). 420,288 index rows in total.

Note: several objects often share a name (e.g. "Porto" = the Portuguese city
and Galician villages). Search by unique `namedObjectId` and check the
coordinates.

## 6. Tools

```bash
# WEB UI (recommended): pick a map, see coverage instantly, search + map after conversion
mib2nds-tool/.venv/bin/python mib2nds-tool/mapui.py        # -> http://127.0.0.1:5000

# convert all .NDS -> .sqlite into _work/nds_out/ (first run: a few minutes, ~30 GB)
mib2nds-tool/.venv/bin/python mib2nds-tool/nds2sqlite.py tree \
    STD2_2510_EU1_202525/maps/00/nds _work/nds_out

# look up a place (EU1 default; --map accepts any folder or .zip)
mib2nds-tool/.venv/bin/python mib2nds-tool/query.py search Nijmegen
mib2nds-tool/.venv/bin/python mib2nds-tool/query.py search "Den Haag" --contains
mib2nds-tool/.venv/bin/python mib2nds-tool/query.py --map STD2_2510_EU_AS_202525.zip countries

# country coverage / render a map
mib2nds-tool/.venv/bin/python mib2nds-tool/query.py countries
mib2nds-tool/.venv/bin/python mib2nds-tool/query.py coverage --out _work/coverage_eu1.png

# compatibility check with the car (default wanted countries, or --wanted)
mib2nds-tool/.venv/bin/python mib2nds-tool/query.py --map STD2_2510_EU_DL2_202525.zip compat
mib2nds-tool/.venv/bin/python mib2nds-tool/query.py --map <map> compat --wanted "NLD,DEU,BEL"
```

The UI (and `query.py --map`) read the country coverage directly from the
small per-region `OVERALL.NDS` (16 KB) files — no full conversion is needed to
know which countries a map covers. Only search + coverage maps convert
`PRODUCT.NDS` (~100 MB, contains the `nameFtsTable`). See
`mib2nds-tool/README.md`.

## 7. Compatibility with MIB2 Standard units

Source: the SEATCUPRA.NET resource "Updating the inbuilt Mib2 Satnav" (thread
p.165 + resource page). MIB2 **Standard** units (part number starting `6P0` /
`5QA`, TomTom cartography) use the DiscoverMedia2 map stream.

- **Map update = maps only; the firmware does NOT need updating.** A 2016
  unit runs the newest Standard maps.
- The map must be from the **Standard stream** (Seat/Skoda Amundsen/VW
  Discover are identical per release). **MIB2 High** packages (Seat Plus /
  Discover Pro, Here cartography) do not work.
- The region must be **ECE** (`SystemName` in `dbinfo.txt`).
- **Mandatory workaround for early releases** (unless the first release is
  2019+): after copying, replace `maps/EEC/EEC_WLD/OVERALL.NDS` with the one
  from the original SD card. The unit is paired to the original release. The
  tool handles this via `car.workaround` in the config (the original
  `OVERALL.NDS` must then be set as `overall_backup`). Also: use the original
  VAG SD card (CID-bound), clear the contents instead of reformatting (FAT32,
  cluster 4096), extract with 7-Zip/Keka, SD slot 1, re-set POIs.
- **Versions (known from the thread)**: 0635=2016/17, 1920=jun 2021
  (exception, excludes the Seat part number), 2510=nov 2025 (ECE 2026),
  2610=jun 2026, 2710=nov 2026 (newest). The tool warns when a newer release
  exists.
- **Coverage 2510 (measured, 8 packages)** vs. the wanted countries
  (NLD/DEU/BEL/LUX/GBR/IRL/FRA/AUT/CHE):

  | package | unpacked | NLD..FRA | AUT/CHE | advice |
  |---|---|---|---|---|
  | EU1 (ECE1) | 8.1 GB | NLD/BEL/LUX/GBR/IRL/FRA ✓, **DEU ✗** | ✗ | misses DE |
  | EU2 (ECE2) | 7.5 GB | none | ✗ | northern/eastern Europe |
  | EU3 (ECE3) | 6.5 GB | only DEU | ✓ | misses NL/BE/LU/GB/IE/FR |
  | EU_DL1 (ECE4) | 10.3 GB | everything except GBR/IRL | ✓ | misses UK/IE |
  | **EU_DL2 (ECE5-equiv.)** | **10.4 GB** | **everything ✓** | **✓** | **recommended** |
  | EU_DL3 (ECE6) | 9.7 GB | everything ✓ | ✗ | alternative + Scandinavia |
  | EU_DL4 (ECE7) | 8.3 GB | none | ✓ | eastern/central + TUR |
  | EU_AS | 18.2 GB | everything ✓ | ✓ | does NOT fit on a 16 GB card |

  Note: the "ECE 5" list on the forum page belongs to the newer 2710 release;
  the 2510 `EU_DL2` by contrast misses PL/CZ/HU/HR/SK/SI but covers all 9
  wanted countries (NL/DE/BE/LU/GB/IE/FR/AT/CH) — hence the recommendation.

## 8. Result in short

- EU1 2026 covers **Spain, Portugal, Gibraltar, Andorra, France, Monaco,
  the Netherlands, Belgium, Luxembourg, Ireland, the United Kingdom and
  Iceland**.
- Search tests: Amsterdam 170×, Rotterdam 138×, Nijmegen 22×, Utrecht 119× in
  E12 — the Netherlands is in.
- The coverage map `_work/coverage_eu1.png` shows all 420k name points per
  region.
