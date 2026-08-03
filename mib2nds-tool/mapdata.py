#!/usr/bin/env python3
"""mapdata.py -- shared logic for the MIB2 map UI/CLI tooling.

Accepts a VW MIB2 NDS map package (an extracted folder that contains a
`maps/` tree, a folder that *is* a maps tree, or a `.zip` of the package),
then:

  * validates the package layout and reads `dbinfo.txt` (fast),
  * extracts country coverage from the tiny per-region `OVERALL.NDS`
    databases (fast, ~16 KB each -- no full conversion needed),
  * on request converts `PRODUCT/PRODUCT.NDS` (contains the `nameFtsTable`)
    to enable place-name search and a coverage map.

All generated files live under `_work/` (throwaway).  Source maps are only
read, never modified.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import zipfile

import matplotlib.path

import ndsgeo
import nds2sqlite
import osutil

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ---------------------------------------------------------------------------
# User configuration (kept out of the repo on purpose).
#
# Resolution order for config.json:
#   1. env MIB2_CONFIG          -> explicit path (recommended for a personal
#                                  config that lives outside the repo),
#   2. <repo>/../config.json    -> "workspace root" pattern (personal data
#                                  folders stay next to the repo),
#   3. <repo>/config.json       -> in-repo config (generic users),
#   4. built-in defaults.
#
# Keys (all optional):
#   dirs.work, dirs.downloads, dirs.backup, dirs.ne_geojson
#   car.make, car.nav_series, car.cartography, car.part_number,
#   car.region_prefix, car.original_release, car.card_size_gb,
#   car.sd_card, car.wanted_countries,
#   car.workaround.enabled, car.workaround.overall_backup
# ---------------------------------------------------------------------------


def _load_config():
    for cand in (
            os.environ.get("MIB2_CONFIG"),
            os.path.join(os.path.dirname(PROJECT_ROOT), "config.json"),
            os.path.join(PROJECT_ROOT, "config.json")):
        if cand and os.path.isfile(cand):
            try:
                with open(cand) as fh:
                    data = json.load(fh)
            except (OSError, ValueError):
                continue
            if isinstance(data, dict) and data:
                return data, cand
    return {}, None


CONFIG_DATA, CONFIG_PATH = _load_config()


def config() -> dict:
    """The raw user config dict (possibly empty)."""
    return CONFIG_DATA


def config_source() -> str:
    """Path of the active config.json, or None when using built-in defaults."""
    return CONFIG_PATH


def _dir_key(key: str, default: str) -> str:
    """Resolve a dirs.* key to an absolute path (relative entries are anchored
    to PROJECT_ROOT)."""
    val = config().get("dirs", {}).get(key) or default
    if not os.path.isabs(val):
        val = os.path.join(PROJECT_ROOT, val)
    return val


WORK = _dir_key("work", "_work")
DOWNLOAD_DIR = _dir_key("downloads", "downloads")
EXTRACTED_DIR = os.path.join(DOWNLOAD_DIR, "extracted")
BACKUP_DIR = _dir_key("backup", "BACKUP")
DEFAULT_NE = (os.environ.get("NE_COUNTRIES")
              or config().get("dirs", {}).get("ne_geojson")
              or os.path.join(tempfile.gettempdir(), "ne_50m.geojson"))

ISO_NAMES = {
    "AD": "Andorra", "AL": "Albania", "AT": "Austria", "BA": "Bosnia and Herzegovina",
    "BE": "Belgium", "BG": "Bulgaria", "BY": "Belarus", "CH": "Switzerland",
    "CY": "Cyprus", "CZ": "Czechia", "DE": "Germany", "DK": "Denmark",
    "EE": "Estonia", "ES": "Spain", "FI": "Finland", "FR": "France",
    "GB": "United Kingdom", "GI": "Gibraltar", "GR": "Greece",
    "HR": "Croatia", "HU": "Hungary", "IE": "Ireland", "IS": "Iceland",
    "IT": "Italy", "LI": "Liechtenstein", "LT": "Lithuania", "LU": "Luxembourg",
    "LV": "Latvia", "MC": "Monaco", "MD": "Moldova", "ME": "Montenegro",
    "MK": "North Macedonia", "MT": "Malta", "NL": "Netherlands", "NO": "Norway",
    "PL": "Poland", "PT": "Portugal", "RO": "Romania", "RS": "Serbia",
    "RU": "Russia", "SE": "Sweden", "SI": "Slovenia", "SK": "Slovakia",
    "TR": "Turkey", "UA": "Ukraine", "XK": "Kosovo", "XXX": "(empty)",
}

ALPHA3_NAMES = {
    "AND": "Andorra", "ALB": "Albania", "AUT": "Austria", "BIH": "Bosnia and Herzegovina",
    "BEL": "Belgium", "BGR": "Bulgaria", "BLR": "Belarus", "CHE": "Switzerland",
    "CYP": "Cyprus", "CZE": "Czechia", "DEU": "Germany", "DNK": "Denmark",
    "EST": "Estonia", "ESP": "Spain", "FIN": "Finland", "FRA": "France",
    "GBR": "United Kingdom", "GIB": "Gibraltar", "GRC": "Greece",
    "HRV": "Croatia", "HUN": "Hungary", "IRL": "Ireland", "ISL": "Iceland",
    "ITA": "Italy", "LTU": "Lithuania", "LUX": "Luxembourg", "LVA": "Latvia",
    "MCO": "Monaco", "MDA": "Moldova", "MNE": "Montenegro", "MKD": "North Macedonia",
    "MLT": "Malta", "NLD": "Netherlands", "NOR": "Norway", "POL": "Poland",
    "PRT": "Portugal", "ROU": "Romania", "SRB": "Serbia", "RUS": "Russia",
    "SWE": "Sweden", "SVN": "Slovenia", "SVK": "Slovakia", "TUR": "Turkey",
    "UKR": "Ukraine", "ARM": "Armenia", "AZE": "Azerbaijan", "GEO": "Georgia",
    "KAZ": "Kazakhstan", "ISR": "Israel", "JOR": "Jordan", "LBN": "Lebanon",
    "SYR": "Syria", "EGY": "Egypt", "LIE": "Liechtenstein",
    "SMR": "San Marino", "VAT": "Vatican City", "XXX": "(empty)",
}

_MEMBER_PREFIX = "00/nds/"

_ISO2_ISO3 = {}
for _i2, _name in ISO_NAMES.items():
    if _name == "(empty)":
        continue
    for _i3, _name3 in ALPHA3_NAMES.items():
        if _name3 == _name:
            _ISO2_ISO3[_i2] = _i3
            break


def _norm_iso(code: str) -> str:
    """Normalize a country code to ISO 3166-1 alpha-3 (2-letter input is
    converted via ISO_NAMES/ALPHA3_NAMES; anything unknown is kept as-is)."""
    code = str(code).upper().strip()
    return _ISO2_ISO3.get(code, code)

def car_profile() -> dict:
    """The reference-car profile: built-in defaults, overridden by config.

    ``car.workaround`` controls the optional "pair the unit to the original
    map release" OVERALL.NDS step (needed for units that came with a very old
    release, e.g. the classic Seat MIB2 Standard cars).
    """
    cfg = config().get("car", {}) or {}
    wa = cfg.get("workaround", {}) or {}
    return {
        "make": cfg.get("make", "MIB2 Standard ECE (DiscoverMedia2 / MST2)"),
        "nav_series": cfg.get("nav_series", "MIB2 Standard (DiscoverMedia2 / MST2)"),
        "cartography": cfg.get("cartography", "TomTom"),
        "part_number": cfg.get("part_number") or "",
        "region_prefix": cfg.get("region_prefix", "ECE"),
        "original_release": cfg.get("original_release") or "",
        "card_size_gb": int(cfg.get("card_size_gb", 16) or 16),
        "sd_card": cfg.get("sd_card", "VAG MIB2 SD card (CID-bound)"),
        "wanted_countries": [_norm_iso(c) for c in (cfg.get("wanted_countries") or [])],
        "workaround": {
            "enabled": bool(wa.get("enabled", False)),
            "overall_backup": (wa.get("overall_backup") or "").strip(),
        },
    }


def overall_backup_path(profile: dict = None) -> str:
    """Absolute path of the configured original OVERALL.NDS, or ''.

    Only meaningful when ``profile['workaround']['enabled']`` is true and an
    ``overall_backup`` path is configured; otherwise '' (workaround not used).
    """
    p = profile or car_profile()
    rel = p["workaround"]["overall_backup"]
    if not p["workaround"]["enabled"] or not rel:
        return ""
    if not os.path.isabs(rel):
        rel = os.path.join(PROJECT_ROOT, rel)
    return rel


# MIB2 Standard (DiscoverMedia2) releases per the SEATCUPRA.NET resource
# "Updating the inbuilt Mib2 Satnav" (page 165 + resource page).
KNOWN_RELEASES = {
    "0635": "2016/17 - first release of the Standard stream",
    "1520": "2019 - first 'lost map' reference release",
    "1920": "jun 2021 - exception: excludes Seat part number, does NOT load on stock firmware",
    "2510": "nov 2025 (ECE 2026)",
    "2610": "jun 2026 (2026/27)",
    "2710": "nov 2026 (2027) - latest",
}


def install_plan(profile: dict = None) -> dict:
    """Build the installation checklist from a profile.

    Returns {"steps": [...], "manual": [...]} where ``manual`` are the steps
    that come *after* the automated copy (eject + re-set POIs) and ``steps``
    is the complete checklist (including the optional OVERALL.NDS workaround
    when the profile enables it).
    """
    p = profile or car_profile()
    wa = p["workaround"]
    steps = [
        "Use the original VAG SD card (or another VAG MIB2 card, 16/32 GB); "
        "a generic SD card is refused (CID-bound).",
        "First copy the full contents of the SD card to the computer as a backup.",
        "Clear the contents of the SD card (no reformat; stay on FAT32, cluster size 4096).",
        "Extract the package with 7-Zip (Windows) or Keka (macOS) and copy the maps/ "
        "contents to the card root.",
    ]
    if wa["enabled"]:
        if wa["overall_backup"]:
            steps.append(
                "On the card, replace maps/EEC/EEC_WLD/OVERALL.NDS with the original "
                f"({wa['overall_backup']}). The unit is paired to the "
                "original map release.")
        else:
            steps.append(
                "On the card, replace maps/EEC/EEC_WLD/OVERALL.NDS with the original "
                "from the car (set car.workaround.overall_backup in the config). "
                "The unit is paired to the original map release.")
    manual = [
        "Eject cleanly via infotainment (Settings > Safely remove > SD1), insert the "
        "card into SD slot 1 while the unit is off, and start navigation.",
        "Re-set your POIs (fuel stations, parking lots etc.); they are "
        "cleared by the update.",
    ]
    return {"steps": steps + manual, "manual": manual}


class MapError(Exception):
    pass


def country_name(iso: str) -> str:
    if len(iso) == 3:
        return ALPHA3_NAMES.get(iso, iso)
    return ISO_NAMES.get(iso, iso)


def _safe_name(name: str) -> str:
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in name)


class FolderSource:
    """A map package on disk: either a folder containing `maps/`, or a maps tree."""
    kind = "folder"

    def __init__(self, path: str):
        path = os.path.abspath(path)
        if not os.path.isdir(path):
            raise MapError(f"not an existing folder: {path}")
        if os.path.isdir(os.path.join(path, "maps")):
            self.package_root = path
            self.maps_root = os.path.join(path, "maps")
        elif os.path.isdir(os.path.join(path, "00")) and os.path.isdir(os.path.join(path, "EEC")):
            self.package_root = os.path.dirname(path)
            self.maps_root = path
        else:
            raise MapError(
                f"{path} does not look like a MIB2 map: expected a folder "
                "containing 'maps/' or a maps tree with '00/' and 'EEC/'")
        base = os.path.basename(os.path.normpath(path))
        self.name = os.path.basename(self.package_root) if base == "maps" else base
        if not self.name:
            self.name = self.package_root
        self._members = None

    def members(self):
        if self._members is None:
            out = []
            for dirpath, _dirs, files in os.walk(self.maps_root):
                for f in files:
                    rel = os.path.relpath(os.path.join(dirpath, f), self.maps_root)
                    out.append(rel.replace(os.sep, "/"))
            self._members = out
        return self._members

    def read(self, rel: str) -> bytes:
        with open(os.path.join(self.maps_root, *rel.split("/")), "rb") as fh:
            return fh.read()

    def md5_manifest(self):
        if os.path.isdir(self.package_root):
            for f in sorted(os.listdir(self.package_root)):
                if f.lower().endswith(".md5sum.txt"):
                    return os.path.join(self.package_root, f)
        return None

    def size_bytes(self) -> int:
        total = 0
        for dirpath, _dirs, files in os.walk(self.maps_root):
            for f in files:
                total += os.path.getsize(os.path.join(dirpath, f))
        return total


class ZipSource:
    """A `.zip` package; members are read directly, nothing is extracted."""
    kind = "zip"

    def __init__(self, path: str):
        path = os.path.abspath(path)
        if not os.path.isfile(path) or not zipfile.is_zipfile(path):
            raise MapError(f"not a valid zip file: {path}")
        self.path = path
        self.name = os.path.splitext(os.path.basename(path))[0]
        self.package_root = os.path.dirname(path)
        self._members = None

    def members(self):
        if self._members is None:
            with zipfile.ZipFile(self.path) as zf:
                self._members = sorted(
                    n[len("maps/"):] for n in zf.namelist()
                    if n.startswith("maps/") and not n.endswith("/"))
        return self._members

    def read(self, rel: str) -> bytes:
        with zipfile.ZipFile(self.path) as zf:
            return zf.read("maps/" + rel)

    def md5_manifest(self):
        return None

    def size_bytes(self) -> int:
        with zipfile.ZipFile(self.path) as zf:
            return sum(i.file_size for i in zf.infolist())


class SevenZipSource:
    """A `.7z` package; members are read on the fly via the 7-Zip binary.
    Nothing is extracted to disk, so it is safe to analyse a multi-GB
    archive without duplicating it in the project."""
    kind = "7z"

    def __init__(self, path: str, binary: str = None):
        path = os.path.abspath(path)
        if not os.path.isfile(path):
            raise MapError(f"not a file: {path}")
        self.path = path
        self.name = os.path.splitext(os.path.basename(path))[0]
        self.package_root = os.path.dirname(path)
        self._members = None
        self._sizes = None
        self.binary = binary or _find_7z()

    def _run(self, args: list, binary: bool = False) -> bytes:
        if not self.binary:
            raise MapError("7-Zip is not installed; cannot read this "
                           ".7z archive")
        env = dict(os.environ, LANG="C", LC_ALL="C")
        try:
            proc = subprocess.run(
                [self.binary] + args, capture_output=True, env=env)
        except OSError as e:
            raise MapError(f"failed to start 7z: {e}")
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout).decode("utf-8", "replace").strip()
            raise MapError(f"7z error (code {proc.returncode}): {err}")
        return proc.stdout if binary else proc.stdout.decode("utf-8", "replace")

    def _index(self):
        if self._members is not None:
            return self._members, self._sizes
        out = self._run(["l", "-slt", self.path])
        members, sizes = [], {}
        cur = None
        for line in out.splitlines():
            if line.startswith("Path = "):
                cur = line[7:]
            elif line.startswith("Size = ") and cur:
                try:
                    sizes[cur] = int(line[7:])
                except ValueError:
                    pass
            elif not line.strip():
                cur = None
        for m, s in sizes.items():
            if m.startswith("maps/") and not m.endswith("/"):
                members.append(m[len("maps/"):])
        self._members = sorted(members)
        self._sizes = {k[len("maps/"):]: v for k, v in sizes.items()
                       if k.startswith("maps/")}
        return self._members, self._sizes

    def members(self):
        return self._index()[0]

    def read(self, rel: str) -> bytes:
        return self._run(["x", "-so", self.path, "maps/" + rel],
                         binary=True)

    def md5_manifest(self):
        return None

    def size_bytes(self) -> int:
        return sum(self._index()[1].values())


def _find_7z() -> str:
    """Locate a 7-Zip binary (backwards-compatible alias of osutil.find_7z)."""
    return osutil.find_7z()


def resolve_source(path: str):
    if os.path.isdir(path):
        return FolderSource(path)
    if os.path.isfile(path) and zipfile.is_zipfile(path):
        return ZipSource(path)
    if os.path.isfile(path) and path.lower().endswith(".7z"):
        return SevenZipSource(path)
    raise MapError(f"path does not exist or is not a folder/zip/7z: {path}")


def region_dirs(source) -> list:
    """Subdirectory names of 00/nds/PRODUCT/ that hold an OVERALL.NDS."""
    prefix = _MEMBER_PREFIX + "PRODUCT/"
    dirs = set()
    for m in source.members():
        if m.startswith(prefix):
            rest = m[len(prefix):]
            if "/" in rest:
                dirs.add(rest.split("/", 1)[0])
    return sorted(dirs)


def validate(source):
    """Check the package layout; return {ok, errors, warnings, info, regions}."""
    errors, warnings = [], []
    members = set(source.members())
    for m in ("00/nds/dbinfo.txt", "00/nds/ROOT.NDS", "00/nds/PRODUCT/PRODUCT.NDS"):
        if m not in members:
            errors.append(f"missing: {m}")
    if "EEC/PRODUCT.NDS" not in members:
        warnings.append("EEC/PRODUCT.NDS missing (regional data only)")
    if "EEC/EEC_WLD/OVERALL.NDS" not in members:
        warnings.append("EEC/EEC_WLD/OVERALL.NDS missing")
    info = {}
    try:
        txt = source.read("00/nds/dbinfo.txt").decode("utf-8", "replace")
        for line in txt.splitlines():
            if "=" in line:
                k, _, v = line.partition("=")
                info[k.strip()] = v.strip().strip('"')
    except Exception as e:
        errors.append(f"dbinfo.txt not readable: {e}")
    regions = region_dirs(source)
    if not regions:
        errors.append("no region directories found under 00/nds/PRODUCT/E*")
    return {"ok": not errors, "errors": errors, "warnings": warnings,
            "info": info, "regions": regions}


def nds_out_dir(source) -> str:
    """Where the converted .sqlite files for this map live (reuses EU1 cache)."""
    eu1_cache = os.path.join(WORK, "nds_out")
    if (source.name == "STD2_2510_EU1_202525"
            and os.path.exists(os.path.join(eu1_cache, "PRODUCT", "PRODUCT.sqlite"))):
        return eu1_cache
    return os.path.join(WORK, "nds_out_" + _safe_name(source.name))


def meta_dir(source) -> str:
    return os.path.join(WORK, "meta_" + _safe_name(source.name))


def _out_path(rel: str) -> str:
    """Map a source member path to its .sqlite path inside nds_out."""
    rel = rel[len(_MEMBER_PREFIX):] if rel.startswith(_MEMBER_PREFIX) else rel
    if rel.lower().endswith(".nds"):
        rel = rel[:-4] + ".sqlite"
    return rel


def _convert(source, rel: str, nds_out: str, log=None) -> bool:
    out_path = os.path.join(nds_out, _out_path(rel))
    if os.path.exists(out_path):
        return False
    data = source.read(rel)
    try:
        key = nds2sqlite.detect_key(data)
    except nds2sqlite.NdsError as e:
        raise MapError(f"{rel}: {e}")
    out = nds2sqlite.convert_bytes(data, key)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "wb") as fh:
        fh.write(out)
    if log:
        log(f"{rel}: key={key.decode(errors='replace') if key else 'none'} -> {out_path}")
    return True


def _country_files(source) -> list:
    rels = []
    for d in region_dirs(source):
        rels.append(f"00/nds/PRODUCT/{d}/OVERALL.NDS")
    rels.append("00/nds/ROOT.NDS")
    if "EEC/PRODUCT.NDS" in source.members():
        rels.append("EEC/PRODUCT.NDS")
    if "EEC/EEC_WLD/OVERALL.NDS" in source.members():
        rels.append("EEC/EEC_WLD/OVERALL.NDS")
    return rels


def ensure_countries(source, nds_out: str, log=None) -> list:
    """Convert only the small files needed for country coverage."""
    converted = []
    for rel in _country_files(source):
        if _convert(source, rel, nds_out, log):
            converted.append(rel)
    return converted


def ensure_search(source, nds_out: str, log=None, progress=None) -> bool:
    """Convert PRODUCT/PRODUCT.NDS (nameFtsTable) if not already done."""
    rel = "00/nds/PRODUCT/PRODUCT.NDS"
    out_path = os.path.join(nds_out, _out_path(rel))
    if os.path.exists(out_path):
        return False
    data = source.read(rel)
    key = nds2sqlite.detect_key(data)
    db_size = int.from_bytes(data[140:148], "big")
    page_size = int.from_bytes(data[172:176], "big")
    total = max(1, db_size // page_size)

    def on_page(_p, n):
        if progress:
            progress(n, total)

    out = nds2sqlite.convert_bytes(data, key, on_page=on_page)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "wb") as fh:
        fh.write(out)
    if log:
        log(f"{rel}: key={key.decode(errors='replace') if key else 'none'} -> {out_path}")
    return True


def read_countries(nds_out: str, regions: list) -> list:
    """isoCountryCode per region dir from its OVERALL.sqlite."""
    out = []
    for d in regions:
        codes = []
        p = os.path.join(nds_out, "PRODUCT", d, "OVERALL.sqlite")
        if os.path.exists(p):
            conn = sqlite3.connect(p)
            try:
                rows = conn.execute(
                    "select distinct isoCountryCode from regionMetadataTable").fetchall()
                codes = sorted({r[0] for r in rows if r[0] and r[0] != "XXX"})
            finally:
                conn.close()
        out.append({"dir": d, "countries": codes})
    return out


def _ver_int(v: str):
    return int(v) if str(v).isdigit() else None


def _covered_iso(source, nds_out: str, regions: list) -> set:
    ensure_countries(source, nds_out)
    return {c for r in read_countries(nds_out, regions) for c in r["countries"]}


def compatibility_check(source, wanted: list = None) -> dict:
    """Does this package fit the configured reference car (MIB2 Standard)?

    Verdict = 'suitable' / 'not suitable'.  Checks:
      series (source name: STD2/DiscoverMedia2/MST2), region (SystemName
      must start with car.region_prefix, default ECE), version (known
      releases), country coverage vs. wanted countries, extracted size vs.
      the SD card (car.card_size_gb).  Plus the install steps (incl. the
      optional OVERALL.NDS workaround from car.workaround).
    """
    profile = car_profile()
    val = validate(source)
    info = val["info"]
    name = source.name
    sys_name = info.get("SystemName", "")
    version = info.get("ApplicationSoftwareVersionNumber", "")
    lname = name.lower()
    wanted = [_norm_iso(c) for c in (list(wanted) if wanted
                                     else list(profile["wanted_countries"]))]

    checks = []

    if "std2" in lname or "discovermedia2" in lname or "mst2" in lname:
        checks.append({
            "label": "Navigation series", "level": "pass", "ok": True,
            "detail": f"MIB2 Standard (DiscoverMedia2/MST2) via the source "
                      f"name '{name}' - correct stream for this unit "
                      "(TomTom)."})
    elif any(k in lname for k in ("mhi2", "discoverpro", "seat plus",
                                  " navi ", "navi pro", "pro_", " high")):
        checks.append({
            "label": "Navigation series", "level": "fail", "ok": False,
            "detail": f"'{name}' looks like an MIB2 High/Plus/Pro package. "
                      "That stream (Here cartography) does NOT work on the "
                      "MIB2 Standard unit."})
    else:
        checks.append({
            "label": "Navigation series", "level": "info", "ok": None,
            "detail": f"Cannot infer the navigation series from the source "
                      f"name '{name}' (expected: STD2/DiscoverMedia2/MST2)."})

    prefix = profile["region_prefix"]
    if sys_name.startswith(prefix):
        checks.append({
            "label": "Region", "level": "pass", "ok": True,
            "detail": f"{sys_name} - {prefix} (Europe), correct market for "
                      "this unit."})
    elif sys_name:
        checks.append({
            "label": "Region", "level": "fail", "ok": False,
            "detail": f"{sys_name} is not a {prefix} package. Non-{prefix} "
                      "maps require an intervention and are not intended "
                      "for this unit."})
    else:
        checks.append({
            "label": "Region", "level": "fail", "ok": False,
            "detail": "SystemName missing in dbinfo.txt."})

    cur = _ver_int(version)
    if cur is not None:
        note = KNOWN_RELEASES.get(version)
        detail = (f"version {version} ({note})" if note
                  else f"version {version} (not in the reference table)")
        newer = [v for v in sorted(KNOWN_RELEASES, key=_ver_int)
                 if _ver_int(v) > cur]
        if newer:
            checks.append({
                "label": "Version", "level": "warn", "ok": False,
                "detail": detail + "; newer releases available: "
                          + ", ".join(newer) + "."})
        else:
            checks.append({
                "label": "Version", "level": "pass", "ok": True,
                "detail": detail + "."})
    else:
        checks.append({
            "label": "Version", "level": "info", "ok": None,
            "detail": "no version number in dbinfo.txt."})

    try:
        covered = _covered_iso(source, nds_out_dir(source), val["regions"])
    except Exception as e:
        covered = set()
        checks.append({
            "label": "Country coverage", "level": "info", "ok": None,
            "detail": f"could not read the country coverage ({e})."})
    missing = [c for c in wanted if c not in covered]
    if not wanted:
        checks.append({
            "label": "Country coverage", "level": "info", "ok": None,
            "detail": "no wanted countries configured (car.wanted_countries "
                      "in the config); only the actual coverage is shown "
                      "above."})
    elif not missing:
        checks.append({
            "label": "Country coverage", "level": "pass", "ok": True,
            "detail": "All wanted countries covered: "
                      + ", ".join(f"{c} ({country_name(c)})" for c in wanted) + "."})
    else:
        checks.append({
            "label": "Country coverage", "level": "warn", "ok": False,
            "detail": "Missing on this map: "
                      + ", ".join(f"{c} ({country_name(c)})" for c in missing)
                      + ". Covered: "
                      + ", ".join(sorted(f"{c} ({country_name(c)})"
                                         for c in wanted if c in covered)) + "."})

    size = source.size_bytes()
    size_gb = size / 1e9
    card_gb = profile["card_size_gb"]
    if size_gb > card_gb - 1.0:
        checks.append({
            "label": "SD card size", "level": "warn", "ok": False,
            "detail": f"{size_gb:.1f} GB extracted does NOT fit on the "
                      f"{card_gb} GB card; use a 32 GB VAG card."})
    else:
        checks.append({
            "label": "SD card size", "level": "pass", "ok": True,
            "detail": f"{size_gb:.1f} GB extracted - fits on the "
                      f"{card_gb} GB card."})

    fails = [c for c in checks if c["level"] == "fail"]
    warns = [c for c in checks if c["level"] == "warn"]
    if fails:
        verdict, verdict_ok = "not suitable", False
    elif warns:
        verdict, verdict_ok = "suitable (with caveats)", True
    else:
        verdict, verdict_ok = "suitable", True

    overall_path = overall_backup_path(profile)
    overall_display = profile["workaround"]["overall_backup"]
    overall_present = bool(overall_path and os.path.isfile(overall_path))
    plan = install_plan(profile)
    steps = list(plan["steps"])
    if profile["workaround"]["enabled"] and not overall_present:
        for i, s in enumerate(steps):
            if "OVERALL.NDS" in s:
                steps[i] = (s + " WARNING: the OVERALL.NDS backup file "
                            "was not found; find it first before updating.")
                break
    install = {
        "steps": steps,
        "manual_steps": list(plan["manual"]),
        "overall_backup": overall_display,
        "overall_backup_present": overall_present,
        "workaround_enabled": profile["workaround"]["enabled"],
    }

    car = {k: v for k, v in profile.items() if k not in ("wanted_countries",)}
    return {
        "verdict": verdict,
        "verdict_ok": verdict_ok,
        "car": car,
        "wanted": wanted,
        "missing": missing,
        "checks": checks,
        "size": {"bytes": size, "gb": round(size_gb, 2)},
        "install": install,
    }


class NEIndex:
    """Natural Earth polygons, filtered to a set of ADM0_A3 codes."""

    def __init__(self, path: str, codes: set):
        self._paths = {}
        self._bbox = {}
        with open(path) as fh:
            data = json.load(fh)
        for feat in data["features"]:
            code = feat["properties"].get("ADM0_A3")
            if not code or code not in codes:
                continue
            geom = feat["geometry"]
            polys = [geom["coordinates"]] if geom["type"] == "Polygon" \
                else geom["coordinates"]
            for poly in polys:
                for ring in poly:
                    if len(ring) < 3:
                        continue
                    pts = [(p[0], p[1]) for p in ring]
                    self._paths.setdefault(code, []).append(matplotlib.path.Path(pts))
                    self._bbox.setdefault(code, []).append(
                        (min(p[0] for p in ring), max(p[0] for p in ring),
                         min(p[1] for p in ring), max(p[1] for p in ring)))

    def countries_at(self, lat: float, lon: float) -> list:
        out = []
        for code, bbs in self._bbox.items():
            for i, (w, e, s, n) in enumerate(bbs):
                if w <= lon <= e and s <= lat <= n:
                    if self._paths[code][i].contains_point((lon, lat)):
                        out.append(code)
                        break
        return out


def infer_region_map(conn, regions: list, ne_path: str) -> dict:
    """Map nameFtsTable updateRegionId -> region dir by geo-sampling points."""
    if not ne_path or not os.path.exists(ne_path):
        return {}
    codes = {c for r in regions for c in r["countries"]}
    idx = NEIndex(ne_path, codes)
    rows = conn.execute(
        "select updateRegionId, mortonCode from nameFtsTable "
        "where mortonCode is not null").fetchall()
    samples = {}
    for r in rows:
        rid = r[0]
        if len(samples.get(rid, [])) >= 400:
            continue
        lat, lon = ndsgeo.morton_to_ll(r[1])
        samples.setdefault(rid, []).append((lat, lon))
    result = {}
    for rid, pts in samples.items():
        counts = {}
        for lat, lon in pts:
            for code in idx.countries_at(lat, lon):
                counts[code] = counts.get(code, 0) + 1
        if not counts:
            continue
        scores = {}
        for r in regions:
            sc = sum(counts.get(c, 0) for c in r["countries"])
            if sc:
                scores[r["dir"]] = sc
        if not scores:
            continue
        best = max(scores, key=scores.get)
        rd = next(r for r in regions if r["dir"] == best)
        result[rid] = {"dir": rd["dir"], "countries": rd["countries"],
                       "label": f"{rd['dir']} ({'/'.join(rd['countries'])})"}
    return result


class Map:
    """A loaded, converted map: connection + region mapping + meta info."""

    def __init__(self, source, nds_out: str, ne_path: str = None):
        self.name = source.name
        self.source = source
        self.nds_out = nds_out
        self.info = validate(source)["info"]
        self.regions = read_countries(nds_out, region_dirs(source))
        db_path = os.path.join(nds_out, "PRODUCT", "PRODUCT.sqlite")
        if not os.path.exists(db_path):
            raise MapError("PRODUCT.sqlite missing; run the conversion first")
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        conf_path = os.path.join(meta_dir(source), "mapconf.json")
        if os.path.exists(conf_path):
            with open(conf_path) as fh:
                self.region_map = {int(k): v for k, v in json.load(fh).items()}
        else:
            self.region_map = infer_region_map(self.conn, self.regions, ne_path)
            os.makedirs(meta_dir(source), exist_ok=True)
            with open(conf_path, "w") as fh:
                json.dump({str(k): v for k, v in self.region_map.items()},
                          fh, indent=2, ensure_ascii=False)

    def region_label(self, update_region_id: int) -> str:
        rm = self.region_map.get(update_region_id)
        if rm:
            return rm["label"]
        return ndsgeo.region_label(update_region_id)

    def covered_iso(self) -> set:
        codes = set()
        for rm in self.region_map.values():
            codes.update(rm["countries"])
        return codes


def search(m: Map, q: str, mode: str = "contains",
           region_filter: list = None, limit: int = 200) -> dict:
    """Place-name lookup in the FTS index. Returns {total, results}."""
    q = (q or "").strip()
    if not q:
        return {"total": 0, "results": []}
    if mode == "exact":
        sql = ("select namedObjectId, mortonCode, updateRegionId, criterionB, criterionC "
               "from nameFtsTable where criterionB = ? COLLATE NOCASE")
        params = [q]
    else:
        sql = ("select namedObjectId, mortonCode, updateRegionId, criterionB, criterionC "
               "from nameFtsTable where criterionB like ? COLLATE NOCASE")
        params = ["%" + q + "%"]
    if region_filter:
        sql += " and updateRegionId in ({})".format(
            ",".join("?" * len(region_filter)))
        params += list(region_filter)
    sql += " limit 5000"
    rows = m.conn.execute(sql, params).fetchall()
    seen = {}
    for r in rows:
        key = r["namedObjectId"]
        if key not in seen:
            lat, lon = ndsgeo.morton_to_ll(r["mortonCode"])
            seen[key] = {
                "namedObjectId": key,
                "name": r["criterionB"],
                "postalCode": r["criterionC"],
                "lat": round(lat, 5),
                "lon": round(lon, 5),
                "updateRegion": r["updateRegionId"],
                "regionLabel": m.region_label(r["updateRegionId"]),
            }
    results = list(seen.values())
    ql = q.lower()
    results.sort(key=lambda it: (it["name"].lower() != ql, it["name"].lower()))
    return {"total": len(results), "results": results[:limit]}


def render_coverage(m: Map, out_path: str, dpi: int = 130,
                    ne_path: str = None) -> dict:
    """Render the Mercator hexbin coverage map. Returns stats."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    rows = m.conn.execute(
        "select mortonCode, updateRegionId from nameFtsTable "
        "where mortonCode is not null").fetchall()
    pts = {}
    lats, lons = [], []
    for r in rows:
        rid = r["updateRegionId"]
        lat, lon = ndsgeo.morton_to_ll(r["mortonCode"])
        pts.setdefault(rid, ([], []))
        pts[rid][0].append(lon)
        pts[rid][1].append(_merc(lat))
        lons.append(lon)
        lats.append(lat)
    if not lats:
        raise MapError("no coordinate points in the name index")
    lon_min, lon_max = min(lons), max(lons)
    lat_min, lat_max = min(lats), max(lats)
    pad_lon = max(0.5, (lon_max - lon_min) * 0.02)
    pad_lat = max(0.5, (lat_max - lat_min) * 0.02)
    lon_min, lon_max = lon_min - pad_lon, lon_max + pad_lon
    lat_min, lat_max = lat_min - pad_lat, lat_max + pad_lat

    fig, ax = plt.subplots(figsize=(15, 12))
    palette = ["#d62728", "#1f77b4", "#2ca02c", "#9467bd", "#ff7f0e",
               "#17becf", "#e377c2", "#8c564b"]
    rid_order = sorted(pts)
    colors = {rid: palette[i % len(palette)] for i, rid in enumerate(rid_order)}

    covered = m.covered_iso()
    if ne_path and os.path.exists(ne_path):
        for poly in _load_ne(ne_path):
            covered_poly = poly["code"] in covered
            for ring in poly["rings"]:
                xs = [p[0] for p in ring]
                ys = [_merc(p[1]) for p in ring]
                ax.plot(xs, ys, color="#666666", lw=0.4, zorder=1)
                if covered_poly:
                    ax.fill(xs, ys, color="#eeee77", alpha=0.45, lw=0, zorder=1)

    stats = {}
    for rid in rid_order:
        xs, ys = pts[rid]
        stats[rid] = {"count": len(xs), "label": m.region_label(rid)}
        ax.hexbin(
            xs, ys, gridsize=120, mincnt=1,
            cmap=plt.cm.colors.LinearSegmentedColormap.from_list(
                "r", [(1, 1, 1, 0), colors[rid]]),
            extent=(lon_min, lon_max, _merc(lat_min), _merc(lat_max)),
            zorder=2, linewidths=0).set_alpha(0.6)

    y_lo, y_hi = _merc(lat_min), _merc(lat_max)
    ax.set_xlim(lon_min, lon_max)
    ax.set_ylim(y_lo, y_hi)
    ax.set_aspect("equal")
    lat_ticks = [t for t in (30, 40, 50, 60, 70) if lat_min < t < lat_max]
    if lat_ticks:
        ax.set_yticks([_merc(t) for t in lat_ticks], [f"{t}N" for t in lat_ticks])
    lon_ticks = [t for t in (-25, -15, -5, 5, 15, 25) if lon_min < t < lon_max]
    if lon_ticks:
        ax.set_xticks(lon_ticks,
                      [f"{t}E" if t >= 0 else f"{-t}W" for t in lon_ticks])
    ax.set_xlabel("longitude")
    ax.set_ylabel("latitude")
    title = f"{m.info.get('SystemName', 'MIB2')} v{m.info.get('ApplicationSoftwareVersionNumber', '?')} - coverage"
    ax.set_title(f"{title}\n{m.name} ({len(rows)} place names in the index)")
    handles = [
        plt.Line2D([], [], marker="s", ls="", color=colors[rid], label=stats[rid]["label"])
        for rid in rid_order
    ]
    ax.legend(handles=handles, loc="lower left", framealpha=0.9)
    fig.set_dpi(dpi)
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    tb = fig.get_tightbbox(renderer)
    ax_in = ax.get_window_extent(renderer).transformed(
        fig.dpi_scale_trans.inverted())
    axrect = {
        "left": (ax_in.x0 - tb.x0) / tb.width,
        "right": (tb.x1 - ax_in.x1) / tb.width,
        "top": (tb.y1 - ax_in.y1) / tb.height,
        "bottom": (ax_in.y0 - tb.y0) / tb.height,
    }
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    fig.patch.set_alpha(0)
    ax.patch.set_alpha(0)
    fig.savefig(out_path, dpi=dpi, bbox_inches=tb, transparent=True)
    ax_path = os.path.join(os.path.dirname(os.path.abspath(out_path)),
                           "coverage_ax.png")
    fig.savefig(ax_path, dpi=dpi, bbox_inches=ax_in, transparent=True)
    plt.close(fig)
    return {
        "bbox": {"lon_min": round(lon_min, 4), "lon_max": round(lon_max, 4),
                 "lat_min": round(lat_min, 4), "lat_max": round(lat_max, 4)},
        "axrect": axrect,
        "regions": stats,
        "total_points": len(rows),
    }


def _merc(lat: float) -> float:
    return ndsgeo.merc(lat)


def _load_ne(path: str):
    with open(path) as fh:
        data = json.load(fh)
    out = []
    for feat in data["features"]:
        code = feat["properties"].get("ADM0_A3")
        admin = feat["properties"].get("ADMIN", "")
        geom = feat["geometry"]
        polys = [geom["coordinates"]] if geom["type"] == "Polygon" \
            else geom["coordinates"]
        for poly in polys:
            rings = [[(p[0], p[1]) for p in ring] for ring in poly]
            out.append({"code": code, "admin": admin, "rings": rings})
    return out


def _dir_size(path: str) -> int:
    total = 0
    for dirpath, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(dirpath, f))
            except OSError:
                pass
    return total


def cleanup_candidates() -> list:
    """Deletable derived/temporary items. Never: BACKUP/, *.zip, *.7z, *.img."""
    backup_abs = os.path.abspath(BACKUP_DIR) + os.sep
    items = []

    def add(path: str, kind: str, label: str):
        if not os.path.lexists(path):
            return
        is_dir = os.path.isdir(path)
        size = _dir_size(path) if is_dir else os.path.getsize(path)
        items.append({"path": os.path.abspath(path), "label": label,
                      "kind": kind, "size": size, "is_dir": is_dir})

    if os.path.isdir(WORK):
        for e in sorted(os.listdir(WORK)):
            add(os.path.join(WORK, e), "work", f"_work/{e}")

    for base in (PROJECT_ROOT, EXTRACTED_DIR):
        if not os.path.isdir(base):
            continue
        for e in sorted(os.listdir(base)):
            p = os.path.join(base, e)
            if not os.path.isdir(p):
                continue
            if os.path.abspath(p).startswith(backup_abs):
                continue
            if os.path.isdir(os.path.join(p, "maps")):
                add(p, "extracted", f"{e}/ (extracted package)")
            elif os.path.isdir(os.path.join(p, "00")) \
                    and os.path.isdir(os.path.join(p, "EEC")):
                add(p, "extracted", f"{e}/ (maps tree)")
    items.sort(key=lambda it: (-it["size"], it["path"]))
    return items


def cleanup_delete(paths) -> dict:
    """Delete only items that pass the same safety filter as the scan.

    Returns {"freed": bytes, "deleted": [path], "errors": [msg]}.
    """
    allowed = {it["path"] for it in cleanup_candidates()}
    freed, deleted, errors = 0, [], []
    for p in (paths or []):
        p = os.path.abspath(str(p))
        if p not in allowed:
            errors.append(f"not allowed: {p}")
            continue
        try:
            size = _dir_size(p) if os.path.isdir(p) else os.path.getsize(p)
            if os.path.isdir(p) and not os.path.islink(p):
                shutil.rmtree(p)
            else:
                os.remove(p)
            freed += size
            deleted.append(p)
        except OSError as e:
            errors.append(f"{p}: {e}")
    return {"freed": freed, "deleted": deleted, "errors": errors}


def find_sources() -> list:
    """Scan the project for selectable map packages (.zip/.7z/folders).

    Searches the project root, downloads/ and downloads/extracted/;
    BACKUP/ is never scanned. Returns [{label, path, kind}]."""
    bases = [PROJECT_ROOT, DOWNLOAD_DIR, EXTRACTED_DIR]
    backup_abs = os.path.abspath(BACKUP_DIR) + os.sep
    cands, seen = [], set()
    for base in bases:
        if not os.path.isdir(base):
            continue
        try:
            entries = sorted(os.listdir(base))
        except OSError:
            continue
        for e in entries:
            p = os.path.join(base, e)
            if os.path.abspath(p).startswith(backup_abs):
                continue
            kind = None
            if os.path.isfile(p) and e.lower().endswith(".zip"):
                kind = "zip"
            elif os.path.isfile(p) and e.lower().endswith(".7z"):
                kind = "7z"
            elif os.path.isdir(p) and os.path.isdir(os.path.join(p, "maps")):
                kind = "folder"
            elif os.path.isdir(p) and os.path.isdir(os.path.join(p, "00")) \
                    and os.path.isdir(os.path.join(p, "EEC")):
                kind = "maps-folder"
            if kind and os.path.abspath(p) not in seen:
                seen.add(os.path.abspath(p))
                cands.append({"label": e, "path": p, "kind": kind})
    cands.sort(key=lambda c: c["label"].lower())
    return cands
