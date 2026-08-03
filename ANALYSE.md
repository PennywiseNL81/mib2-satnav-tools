# ANALYSE.md — MIB2-kaartdata v2510 EU1 (NDS)

Analyse van het nieuwe kaartpakket `STD2_2510_EU1_202525` (mapversie **2510**, ECE1 2026).
Doel: kunnen opzoeken of een plaats op de kaart staat + inzicht in de landdekking.

## 1. Opbouw van de NDS-data

De kaartdata staat onder `maps/00/nds/` en `maps/EEC/`:

```
maps/00/nds/
├── ROOT.NDS                      # productcatalogus (NIET versleuteld)
├── dbinfo.txt                    # SystemName="ECE1 2026", ApplicationSoftwareVersionNumber="2510"
└── PRODUCT/
    ├── PRODUCT.NDS               # productdatabase: building blocks, update regions, versies, FTS-index
    ├── E1/   E2/   E3/   E12/    # regionale bouwblokken (update regions 0, 6, 7, 3)
    │   ├── OVERALL.NDS           # regio-metadata (landcodes per regio)
    │   ├── NAME.NDS              # namenbouwblok (straten/wegen, 2,36M records in E1)
    │   ├── BMD.NDS / ROUTING.NDS # basiskaartdisplay / routing
    │   ├── POI.NDS / FTS.NDS / SLI.NDS / SPEECH.NDS / TI.NDS / JV.NDS
maps/EEC/
├── PRODUCT.NDS                   # Europees product (NIET versleuteld)
└── EEC_WLD/ (BMD.NDS, OVERALL.NDS)   # coarse "Europa + wereld"-basis
```

## 2. Landdekking (geverifieerd)

Uit `OVERALL.sqlite` per regio (`regionMetadataTable.isoCountryCode`):

| update region | mapmap | landen |
|---|---|---|
| 0 | E1 | **PRT** (Portugal), **GIB** (Gibraltar), **ESP** (Spanje) |
| 6 | E2 | **MCO** (Monaco), **FRA** (Frankrijk), **AND** (Andorra) |
| 7 | E3 | **ISL** (IJsland), **IRL** (Ierland), **GBR** (VK) |
| 3 | E12 | **NLD** (Nederland), **LUX** (Luxemburg), **BEL** (België) |

De speech-databases in `maps/00/sds/` (AD, BE, ES, FR, GB, GI, IE, IS, LU, MC, NL, PT)
komen 1-op-1 overeen met deze 12 landen. **Conclusie: Nederland/België/Luxemburg zitten
in de EU1-kaart (regio E12).**

## 3. Bestandsformaat: SQLite via ZipVFS + AES

Elk `.NDS`-bestand is een SQLite-database verpakt in het ZipVFS-containerformaat:

| offset | veld |
|---|---|
| 0 | `ZVZL`-signature |
| 108 | `dataStart` (8 bytes, big-endian) |
| 140 | `dbSize` (8 bytes) |
| 172 | `pageSize` (= 65536) |
| 176 | versie |
| 200 | pagemap: 8-byte-entries per pagina `offset = e>>24`, `size = (e>>7)&0x1FFFF` |

Per pagina: 6-byte slot-header (`pageno = u32>>1`, `pagelen = u32@2 & 0x1FFFF`), daarna
de payload op `offset+6` → **zlib** → 64 KiB SQLite-pagina.

**Encryptie**: AES-128-**ECB**, geen padding, alleen de eerste 64 bytes van elke zlib-payload
(zodat het zlib-deel onversleuteld blijft). De sleutel voor dit pakket is
`z463rTyK9YS3JIPq` en wordt automatisch gevonden door alle 12 bekende VAG-sleutels te
proberen. `ROOT.NDS` en `maps/EEC/PRODUCT.NDS` zijn onversleuteld.

Referenties: `pcbbc/NDS2SQLite` (C#), `lprot/MIB-Tools` (Python),
`ratcashdev/zipvfs-converter` (issue #4: VAG MIB2-sleutels).

## 4. Coördinaten: de NDS-mortoncode

De full-text-naamindex `nameFtsTable` (in `PRODUCT/PRODUCT.sqlite`) bevat per naam een
`mortonCode` (64-bit). Decoderen naar breedtegraad/lengtegraad:

```
x = interleave(even bits van morton, LSB-first)   # 32-bit signed
y = interleave(oneven bits, LSB-first)            # 31-bit, y>=2^30 → y-=2^31
lat = y * 360 / 2^32
lon = x * 360 / 2^32
```

(of simpelweg `ndsgeo.morton_to_ll(morton)`). Dit is de officiële NDS-definitie
(`ndsmath::MortonCode`). Validatie:

| plaats | gedecodeerd | werkelijk | fout |
|---|---|---|---|
| Madrid | 40.4167, -3.7003 | 40.4168, -3.7038 | < 0.004° |
| Amsterdam | 52.3732, 4.8907 | 52.3676, 4.9041 | < 0.014° |
| London | 51.5002, -0.1262 | 51.5074, -0.1278 | < 0.008° |
| Paris | 48.8569, 2.3508 | 48.8566, 2.3522 | < 0.002° |
| Rotterdam | 51.9229, 4.4706 | 51.9225, 4.4792 | < 0.009° |
| Nijmegen | 51.8417, 5.8586 | 51.8424, 5.8528 | < 0.006° |

## 5. De naamindex (`nameFtsTable`)

Kolommen: `namedObjectId`, `mortonCode`, `updateRegionId`, `criterionA`..`criterionJ`.
Gebruik: `criterionB` = plaatsnaam, `criterionC` = postcode, `criterionA` = region-verwijzing.
Per object meerdere rijen (taalvarianten). Totaal 420.288 indexrijen.

Let op: er zijn vaak meerdere objecten met dezelfde naam (bijv. "Porto" = de Portugese stad
én Galicische dorpen). Zoek dus op unieke `namedObjectId` en controleer de coördinaten.

## 6. Tools

```bash
# WEB-UI (aanbevolen): map selecteren, landdekking direct, zoeken + kaart na conversie
mib2nds-tool/.venv/bin/python mib2nds-tool/mapui.py        # -> http://127.0.0.1:5000

# conversie van alle .NDS -> .sqlite in _work/nds_out/ (eerste keer: enkele minuten, ~30 GB)
mib2nds-tool/.venv/bin/python mib2nds-tool/nds2sqlite.py tree \
    STD2_2510_EU1_202525/maps/00/nds _work/nds_out

# plaats opzoeken (EU1 default; --map accepteert elke folder of .zip)
mib2nds-tool/.venv/bin/python mib2nds-tool/query.py search Nijmegen
mib2nds-tool/.venv/bin/python mib2nds-tool/query.py search "Den Haag" --contains
mib2nds-tool/.venv/bin/python mib2nds-tool/query.py --map STD2_2510_EU_AS_202525.zip countries

# landdekking / kaart genereren
mib2nds-tool/.venv/bin/python mib2nds-tool/query.py countries
mib2nds-tool/.venv/bin/python mib2nds-tool/query.py coverage --out _work/coverage_eu1.png

# compatibiliteitscheck met de auto (default gewenste landen, of --wanted)
mib2nds-tool/.venv/bin/python mib2nds-tool/query.py --map STD2_2510_EU_DL2_202525.zip compat
mib2nds-tool/.venv/bin/python mib2nds-tool/query.py --map <map> compat --wanted "NLD,DEU,BEL"
```

De UI (en `query.py --map`) lezen de landdekking direct uit de kleine
`OVERALL.NDS` (16 KB) per regio — geen volledige conversie nodig om te weten
welke landen op een kaart staan. Alleen voor zoeken + dekkingskaart wordt
`PRODUCT.NDS` (~100 MB, bevat de `nameFtsTable`) geconverteerd. Zie
`mib2nds-tool/README.md`.

## 7. Compatibiliteit met MIB2 Standard-eenheden

Bron: SEATCUPRA.NET-resource "Updating the inbuilt Mib2 Satnav" (thread p.165
+ resource-pagina). MIB2 **Standard**-eenheden (partnummer begint met
`6P0`/`5QA`, TomTom-cartografie) gebruiken de DiscoverMedia2-kaartstroom.

- **Kaartupdate = maps only; firmware hoeft NIET geüpdatet.** Een 2016-unit
  draait de nieuwste Standard-kaarten.
- Kaart moet van de **Standard-stroom** zijn (Seat/Skoda Amundsen/VW Discover
  zijn per release identiek). **MIB2 High**-pakketten (Seat Plus / Discover
  Pro, Here-cartografie) werken niet.
- Regio moet **ECE** zijn (`SystemName` in `dbinfo.txt`).
- **Verplichte workaround voor vroege releases** (tenzij eerste release
  2019+): na het kopiëren `maps/EEC/EEC_WLD/OVERALL.NDS` vervangen door die
  van de originele SD-kaart. De unit is gekoppeld aan de oorspronkelijke
  release. De tool regelt dit via `car.workaround` in de config (de
  originele `OVERALL.NDS` moet dan als `overall_backup` zijn opgegeven).
  Verder: originele VAG-SD-kaart (CID-gebonden), inhoud wissen i.p.v.
  herformatteren (FAT32, cluster 4096), 7-Zip/Keka uitpakken, SD-slot 1,
  POI's opnieuw instellen.
- **Versies (bekend uit de thread)**: 0635=2016/17, 1920=jun 2021
  (uitzondering, excl. Seat-partnummer), 2510=nov 2025 (ECE 2026), 2610=jun
  2026, 2710=nov 2026 (nieuwste). De tool waarschuwt als er nieuwer bestaat.
- **Dekking 2510 (gemeten, 8 pakketten)** t.o.v. de gewenste landen
  (NLD/DEU/BEL/LUX/GBR/IRL/FRA/AUT/CHE):

  | pakket | uitpak | NLD..FRA | AUT/CHE | advies |
  |---|---|---|---|---|
  | EU1 (ECE1) | 8,1 GB | NLD/BEL/LUX/GBR/IRL/FRA ✓, **DEU ✗** | ✗ | mist DE |
  | EU2 (ECE2) | 7,5 GB | geen | ✗ | noord/oost-Europa |
  | EU3 (ECE3) | 6,5 GB | alleen DEU | ✓ | mist NL/BE/LU/GB/IE/FR |
  | EU_DL1 (ECE4) | 10,3 GB | alles behalve GBR/IRL | ✓ | mist UK/IE |
  | **EU_DL2 (ECE5-equiv.)** | **10,4 GB** | **alles ✓** | **✓** | **aanbevolen** |
  | EU_DL3 (ECE6) | 9,7 GB | alles ✓ | ✗ | alternatief + Scandinavië |
  | EU_DL4 (ECE7) | 8,3 GB | geen | ✓ | oost/centraal + TUR |
  | EU_AS | 18,2 GB | alles ✓ | ✓ | past NIET op 16 GB-kaart |

  Let op: de "ECE 5"-lijst op de forum-pagina hoort bij de nieuwere 2710-
  release; de 2510 `EU_DL2` mist daartegenover PL/CZ/HU/HR/SK/SI, maar dekt
  alle 9 gewenste landen (NL/DE/BE/LU/GB/IE/FR/AT/CH) — vandaar de
  aanbeveling.

## 8. Resultaat in het kort

- EU1 2026 dekt **Spanje, Portugal, Gibraltar, Andorra, Frankrijk, Monaco, Nederland,
  België, Luxemburg, Ierland, Verenigd Koninkrijk en IJsland**.
- Zoektests: Amsterdam 170×, Rotterdam 138×, Nijmegen 22×, Utrecht 119× in E12 — Nederland zit erin.
- De dekkingskaart `_work/coverage_eu1.png` toont alle 420k naam-punten per regio.
