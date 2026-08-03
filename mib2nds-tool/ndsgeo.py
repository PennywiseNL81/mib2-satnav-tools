"""Shared helpers for MIB2 NDS query/coverage tooling."""

import math
import os
import sqlite3

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_PRODUCT_DB = os.environ.get(
    "NDS_PRODUCT_DB",
    os.path.join(_REPO_ROOT, "_work", "nds_out", "PRODUCT", "PRODUCT.sqlite"),
)

# updateRegionId (nameFtsTable) -> NDS region dir + covered countries.
REGION_INFO = {
    0: {"nds_dir": "E1", "countries": ["PRT", "GIB", "ESP"], "label": "E1 (PT/GI/ES)"},
    3: {"nds_dir": "E12", "countries": ["NLD", "LUX", "BEL"], "label": "E12 (NL/LU/BE)"},
    6: {"nds_dir": "E2", "countries": ["MCO", "FRA", "AND"], "label": "E2 (MC/FR/AD)"},
    7: {"nds_dir": "E3", "countries": ["ISL", "IRL", "GBR"], "label": "E3 (IS/IE/GB)"},
}

# ISO 3166-1 alpha-3 (ADM0_A3 in Natural Earth) for the covered countries.
COVERED_ISO = sorted({c for info in REGION_INFO.values() for c in info["countries"]})


def morton_to_ll(morton: int) -> tuple:
    """Decode an NDS morton code (see ndsmath::MortonCode) to (lat, lon) degrees."""
    bit = 1
    x = 0
    y = 0
    for _ in range(31):
        x |= morton & bit
        morton >>= 1
        y |= morton & bit
        bit <<= 1
    x |= morton & bit
    if y >= (1 << 30):
        y -= 1 << 31
    if x >= (1 << 31):
        x -= 1 << 32
    lat = y * 360.0 / (1 << 32)
    lon = x * 360.0 / (1 << 32)
    return lat, lon


def ll_to_morton(lat: float, lon: float) -> int:
    """Encode (lat, lon) degrees to an NDS morton code (roundtrip check)."""
    scale = (1 << 32) / 360.0
    x = int(round(lon * scale))
    y = int(round(lat * scale))
    xb = (1 << 31)
    yb = (1 << 30)
    while x >= xb:
        x -= 1 << 32
    while x < -xb:
        x += 1 << 32
    while y >= yb:
        y -= 1 << 31
    while y < -yb:
        y += 1 << 31
    m = 0
    bit = 1
    y <<= 1
    for _ in range(31):
        m |= x & bit
        x <<= 1
        bit <<= 1
        m |= y & bit
        y <<= 1
        bit <<= 1
    m |= x & bit
    return m & ~(1 << 63)


def open_product(db_path: str = DEFAULT_PRODUCT_DB):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def merc(lat: float) -> float:
    """Mercator y (in degrees) for a latitude, so maps are conformal."""
    return math.degrees(math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)))


def region_label(update_region_id: int) -> str:
    return REGION_INFO.get(update_region_id, {}).get("label", f"region {update_region_id}")


def haversine(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))
