#!/usr/bin/env python3
"""updates.py -- update-check + download for MIB2 Standard map packages.

The registry (updates.json in the project root) lists known releases with
their direct download URLs on the VW navigation-maps server. Those URLs are
published on the SEATCUPRA.NET resource "Updating the inbuilt Mib2 Satnav"
(and Briskoda / TX-Board / MHH-AUTO) whenever a new map release arrives.

  * check_remote()  verifies each URL is still online (HEAD / Range GET).
  * discover_new()  probes the official VW server for newer releases by
    walking the URL pattern (year-path x version x region) with HEAD probes.
    The server hosts only the newest release, so a 200 hit is a live package.
  * download()      streams a package into downloads/ with resume support.
  * The SD-updater (sd-updater/) picks packages from downloads/, it never
    downloads anything itself.

mib-helper.com blocks bots (HTTP 403), so no scraping is attempted there;
new releases are normally added to updates.json by hand, but discover_new()
automates the "official place" check.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request

import mapdata

UPDATES_JSON = os.path.join(mapdata.PROJECT_ROOT, "updates.json")
DOWNLOAD_DIR = mapdata.DOWNLOAD_DIR

# URL pattern on the official VW server:
#   https://navigation-maps.volkswagen.com/vw-maps/<year-path>/DiscoverMedia2_<region>_<ver>_V<build>.7z
VW_BASE = "https://navigation-maps.volkswagen.com/vw-maps/"
REGIONS = ["EU1", "EU2", "EU3", "EU-DL1", "EU-DL2", "EU-DL3", "EU-DL4",
           "EU-AS"]
YEAR_PATHS = ["Update_24_25", "Update_25_26", "Update_26_27",
              "Update_27_28", "Update_28_29"]
VERSIONS = ["2410", "2510", "2610", "2710", "2810", "2910", "3010"]
CANARY_REGION = "EU-DL2"

USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120 Safari/537.36")

SOURCE_FORUM = ("https://www.seatcupra.net/forums/threads/"
                "updating-the-inbuilt-mib2-satnav-mib2-tricks-and-mib1.388586/"
                "page-165")


def load_registry() -> dict:
    if os.path.exists(UPDATES_JSON):
        try:
            with open(UPDATES_JSON) as fh:
                return json.load(fh)
        except (OSError, ValueError):
            pass
    return {"packages": [], "source_forum": SOURCE_FORUM}


def save_registry(data: dict) -> None:
    with open(UPDATES_JSON, "w") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)


def _urlopen(req, timeout: float):
    return urllib.request.urlopen(req, timeout=timeout)


def http_probe(url: str, timeout: float = 15) -> tuple:
    """Return (ok, size_bytes|None). Tries HEAD, then a 1-byte Range GET."""
    def req_with(method=None, extra_headers=None):
        headers = {"User-Agent": USER_AGENT}
        if extra_headers:
            headers.update(extra_headers)
        return urllib.request.Request(url, headers=headers, method=method)

    try:
        with _urlopen(req_with("HEAD"), timeout) as r:
            cl = r.headers.get("Content-Length")
            size = int(cl) if cl and cl.isdigit() else None
            return True, size
    except urllib.error.HTTPError as e:
        if e.code not in (403, 405, 501):
            return False, None
    except Exception:
        return False, None
    try:
        with _urlopen(req_with("GET", {"Range": "bytes=0-0"}), timeout) as r:
            cr = r.headers.get("Content-Range", "")
            size = None
            if "/" in cr:
                try:
                    size = int(cr.rsplit("/", 1)[1])
                except ValueError:
                    pass
            if size is None:
                cl = r.headers.get("Content-Length")
                if cl and cl.isdigit():
                    size = int(cl)
            return True, size
    except Exception:
        return False, None


def local_path(pkg: dict):
    fname = os.path.basename(pkg.get("url", ""))
    p = os.path.join(DOWNLOAD_DIR, fname)
    return p if os.path.isfile(p) else None


def enrich(pkg: dict, do_check: bool = True) -> dict:
    out = dict(pkg)
    out["filename"] = os.path.basename(pkg.get("url", ""))
    lp = local_path(pkg)
    out["local"] = lp
    out["local_size"] = os.path.getsize(lp) if lp else None
    out["local_recommended"] = bool(
        out.get("recommended") and lp and os.path.getsize(lp) > 0)
    if do_check:
        online, size = http_probe(pkg.get("url", ""))
        out["online"] = online
        out["remote_size"] = size
    else:
        out["online"] = None
        out["remote_size"] = None
    return out


def registry_status(do_check: bool = True) -> dict:
    data = load_registry()
    packages = [enrich(p, do_check) for p in data.get("packages", [])]
    return {
        "packages": packages,
        "source_forum": data.get("source_forum", SOURCE_FORUM),
        "download_dir": DOWNLOAD_DIR,
        "checked_at": time.strftime("%Y-%m-%d %H:%M:%S") if do_check else None,
    }


def download(url: str, progress=None, log=None) -> str:
    """Stream a package into downloads/, resuming a trailing `.part` file.

    Returns the final path. Raises on failure.
    """
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    fname = os.path.basename(url.split("?")[0])
    part = os.path.join(DOWNLOAD_DIR, fname + ".part")
    final = os.path.join(DOWNLOAD_DIR, fname)
    headers = {"User-Agent": USER_AGENT}
    existing = os.path.getsize(part) if os.path.exists(part) else 0
    if existing:
        headers["Range"] = f"bytes={existing}-"
    req = urllib.request.Request(url, headers=headers)
    with _urlopen(req, timeout=30) as r:
        if log:
            log(f"download gestart: {fname} (resume vanaf {existing} bytes)")
        total = existing
        if r.status == 206:
            cr = r.headers.get("Content-Range", "")
            if "/" in cr:
                try:
                    total = int(cr.rsplit("/", 1)[1])
                except ValueError:
                    pass
        else:
            cl = r.headers.get("Content-Length")
            if cl and cl.isdigit():
                total = existing + int(cl)
        mode = "ab" if existing and r.status == 206 else "wb"
        done = existing if mode == "ab" else 0
        with open(part, mode) as fh:
            while True:
                chunk = r.read(1024 * 256)
                if not chunk:
                    break
                fh.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
    os.replace(part, final)
    if log:
        log(f"download klaar: {fname} ({total} bytes)")
    return final


def _sort_year(path: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", path))


def _discover_candidates() -> tuple:
    """Extend the default grid with anything already in the registry."""
    data = load_registry()
    paths = set(YEAR_PATHS)
    vers = set(VERSIONS)
    build = "V24"
    for p in data.get("packages", []):
        url = p.get("url", "")
        m = re.search(r"/vw-maps/(Update_\d+_\d+)/", url)
        if m:
            paths.add(m.group(1))
        m = re.search(r"_(\d{4})_V", url)
        if m:
            vers.add(m.group(1))
        m = re.search(r"_V(\d+)\.7z", url)
        if m:
            build = f"V{m.group(1)}"
    return (sorted(paths, key=_sort_year),
            sorted(vers, key=int, reverse=True),
            build)


def _release_label(version: str, build: str) -> str:
    return f"{2000 + int(version) // 10} ({build})"


def discover_new(add: bool = False, progress=None) -> dict:
    """Probe the official VW server for map packages, newest first.

    Probes the canary region (EU-DL2) across the year-path x version grid
    (HEAD requests, no downloads); every 200 hit is expanded to all regions.
    Returns the found packages; with ``add=True`` any version newer than the
    registry's current max is appended to updates.json.
    """
    data = load_registry()
    current_max = 0
    for p in data.get("packages", []):
        m = re.search(r"_(\d{4})_V", p.get("url", ""))
        if m:
            current_max = max(current_max, int(m.group(1)))
    paths, vers, build = _discover_candidates()
    total = len(paths) * len(vers)
    checked = 0
    found = []
    seen = set()
    for yp in paths:
        for v in vers:
            canary = f"{VW_BASE}{yp}/DiscoverMedia2_{CANARY_REGION}_{v}_{build}.7z"
            ok, _size = http_probe(canary)
            checked += 1
            if progress:
                progress(checked, total)
            if not ok:
                continue
            for region in REGIONS:
                url = (f"{VW_BASE}{yp}/DiscoverMedia2_{region}_{v}_{build}.7z")
                ok2, size2 = http_probe(url)
                checked += 1
                if progress:
                    progress(checked, total)
                if not ok2 or url in seen:
                    continue
                seen.add(url)
                found.append({"url": url, "region": region, "version": v,
                              "size": size2,
                              "release": _release_label(v, build)})
    new = sorted((f for f in found if int(f["version"]) > current_max),
                 key=lambda f: (-int(f["version"]), f["region"]))
    known = [f for f in found if int(f["version"]) <= current_max]
    added = []
    if add and new:
        for f in new:
            if any(p.get("url") == f["url"]
                   for p in data.get("packages", [])):
                continue
            data.setdefault("packages", []).append({
                "id": f"{f['version']}-{f['region'].lower()}",
                "version": f["version"],
                "release": f["release"],
                "region": f["region"],
                "url": f["url"],
                "size_gb": round(f["size"] / 1e9, 2) if f["size"] else None,
                "countries": [],
                "note": "Automatisch gevonden op de officiële VW-server; "
                        "landdekking bevestigen na selectie",
                "recommended": False,
            })
        save_registry(data)
        added = new
    return {
        "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "probed": checked,
        "current_max": str(current_max),
        "build": build,
        "year_paths": paths,
        "versions": vers,
        "found": found,
        "known": known,
        "new_releases": new,
        "added": added,
        "registry_packages": len(data.get("packages", [])),
    }
