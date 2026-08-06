#!/usr/bin/env python3
"""mapui.py -- local web UI to check MIB2 map packages.

Pick a downloaded map (folder with `maps/`, a `.zip` or a `.7z`), get an
instant country-coverage check, and optionally convert it to enable
place-name search and a coverage map. Also:

  * Update-check: known releases + download links (VW navigation-maps
    server), with an online probe and a resumable download into downloads/.
  * SD-updater tab: backup + install a chosen package onto the MIB2 SD
    card (rsync + 7z), including the Seat OVERALL.NDS workaround.
  * Cleanup tab: remove derived data in _work/ and extracted packages.

Run:
    mib2nds-tool/.venv/bin/python mib2nds-tool/mapui.py [--port 5000]
Then open http://127.0.0.1:5000
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import traceback
import uuid
import webbrowser

from werkzeug.serving import make_server

from flask import Flask, jsonify, render_template, request, send_file

import mapdata
import osutil
import updates

sys.path.insert(0, os.path.join(mapdata.PROJECT_ROOT, "sd-updater"))
import update_sd  # noqa: E402

app = Flask(__name__)

_SERVER = None
app.json.ensure_ascii = False

JOBS = {}
JOBS_LOCK = threading.Lock()


def _expand(path):
    if not path:
        return ""
    return os.path.expanduser(str(path).strip().strip('"').strip("'"))


def _search_ready(nds_out):
    return os.path.exists(os.path.join(nds_out, "PRODUCT", "PRODUCT.sqlite"))


def _load_map(path):
    source = mapdata.resolve_source(path)
    nds_out = mapdata.nds_out_dir(source)
    if not _search_ready(nds_out):
        raise mapdata.MapError(
            "this map has not been converted yet; click "
            "'Enable search + coverage map' first")
    return mapdata.Map(source, nds_out, ne_path=mapdata.DEFAULT_NE)


def _find_candidates():
    """Scan the project (root, downloads/, downloads/extracted/) for packages."""
    cands = []
    for s in mapdata.find_sources():
        try:
            src = mapdata.resolve_source(s["path"])
            name = src.name
        except mapdata.MapError:
            name = mapdata._safe_name(s["label"])
        if name == "STD2_2510_EU1_202525" and _search_ready(
                os.path.join(mapdata.WORK, "nds_out")):
            ready = True
        else:
            ready = _search_ready(os.path.join(mapdata.WORK,
                                               "nds_out_" + name))
        cands.append({"label": s["label"], "path": s["path"],
                      "kind": s["kind"], "search_ready": ready})
    return cands


@app.get("/")
def index():
    return render_template("map.html", candidates=_find_candidates(),
                           root=mapdata.PROJECT_ROOT,
                           downloads=mapdata.DOWNLOAD_DIR,
                           extracted=mapdata.EXTRACTED_DIR,
                           version=mapdata.__version__)


@app.get("/api/maps")
def api_maps():
    return jsonify({"ok": True, "candidates": _find_candidates(),
                    "root": mapdata.PROJECT_ROOT})


@app.post("/api/select")
def api_select():
    data = request.get_json(force=True, silent=True) or {}
    path = _expand(data.get("path"))
    if not path:
        return jsonify({"ok": False, "error": "provide a path"}), 400
    if not os.path.exists(path):
        return jsonify({"ok": False, "error": f"path does not exist: {path}"}), 400
    try:
        source = mapdata.resolve_source(path)
    except mapdata.MapError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    val = mapdata.validate(source)
    if not val["ok"]:
        return jsonify({"ok": False, "error": "; ".join(val["errors"]),
                        "warnings": val["warnings"], "info": val["info"],
                        "name": source.name, "kind": source.kind, "path": path})
    nds_out = mapdata.nds_out_dir(source)
    try:
        mapdata.ensure_countries(source, nds_out)
    except Exception as e:
        return jsonify(
            {"ok": False, "error": f"country check failed: {e}\n"
             f"{traceback.format_exc()}"}), 500
    regions = mapdata.read_countries(nds_out, val["regions"])
    for r in regions:
        r["countries_display"] = [f"{c} ({mapdata.country_name(c)})"
                                  for c in r["countries"]]
    all_codes = sorted({c for r in regions for c in r["countries"]})
    wanted = data.get("wanted")
    if wanted is not None:
        wanted = [str(w).upper().strip() for w in wanted if str(w).strip()]
    try:
        compat = mapdata.compatibility_check(source, wanted)
    except Exception as e:
        return jsonify(
            {"ok": False, "error": f"compatibility check failed: {e}\n"
             f"{traceback.format_exc()}"}), 500
    return jsonify({
        "ok": True,
        "name": source.name,
        "kind": source.kind,
        "path": path,
        "info": val["info"],
        "warnings": val["warnings"],
        "regions": regions,
        "countries": [{"code": c, "name": mapdata.country_name(c)}
                      for c in all_codes],
        "compat": compat,
        "search_ready": _search_ready(nds_out),
    })


@app.post("/api/compat")
def api_compat():
    data = request.get_json(force=True, silent=True) or {}
    path = _expand(data.get("path"))
    if not path or not os.path.exists(path):
        return jsonify({"ok": False, "error": "path does not exist"}), 400
    try:
        source = mapdata.resolve_source(path)
    except mapdata.MapError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    wanted = data.get("wanted")
    if wanted is not None:
        wanted = [str(w).upper().strip() for w in wanted if str(w).strip()]
    try:
        compat = mapdata.compatibility_check(source, wanted)
    except Exception as e:
        return jsonify(
            {"ok": False, "error": f"compatibility check failed: {e}\n"
             f"{traceback.format_exc()}"}), 500
    return jsonify({"ok": True, "compat": compat})


@app.post("/api/convert")
def api_convert():
    data = request.get_json(force=True, silent=True) or {}
    path = _expand(data.get("path"))
    if not path or not os.path.exists(path):
        return jsonify({"ok": False, "error": "path does not exist"}), 400
    try:
        source = mapdata.resolve_source(path)
    except mapdata.MapError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    nds_out = mapdata.nds_out_dir(source)
    if _search_ready(nds_out):
        return jsonify({"ok": True, "search_ready": True})
    job_id = uuid.uuid4().hex
    state = {"state": "running", "done": 0, "total": 0, "log": [],
             "error": None, "started": time.time()}
    with JOBS_LOCK:
        JOBS[job_id] = state

    def run():
        try:
            def log(line):
                with JOBS_LOCK:
                    state["log"].append(line)

            def progress(done, total):
                with JOBS_LOCK:
                    state["done"] = done
                    state["total"] = total

            mapdata.ensure_search(source, nds_out, log=log, progress=progress)
            with JOBS_LOCK:
                state["state"] = "done"
        except Exception as e:
            with JOBS_LOCK:
                state["state"] = "error"
                state["error"] = f"{e}\n{traceback.format_exc()}"

    threading.Thread(target=run, daemon=True).start()
    return jsonify({"ok": True, "job": job_id})


@app.get("/api/status/<job_id>")
def api_status(job_id):
    with JOBS_LOCK:
        state = JOBS.get(job_id)
    if state is None:
        return jsonify({"ok": False, "error": "unknown job"}), 404
    return jsonify({"ok": True, **state, "log_tail": state["log"][-40:]})


@app.get("/api/cleanup")
def api_cleanup_list():
    items = mapdata.cleanup_candidates()
    return jsonify({"ok": True, "items": items,
                    "total_bytes": sum(i["size"] for i in items)})


@app.post("/api/cleanup")
def api_cleanup_delete():
    data = request.get_json(force=True, silent=True) or {}
    res = mapdata.cleanup_delete(data.get("paths") or [])
    return jsonify({"ok": True, **res})


@app.get("/api/updates")
def api_updates():
    do_check = request.args.get("check") != "0"
    return jsonify({"ok": True, **updates.registry_status(do_check=do_check)})


@app.post("/api/discover")
def api_discover():
    data = request.get_json(force=True, silent=True) or {}
    add = bool(data.get("add"))
    job_id = uuid.uuid4().hex
    state = {"state": "running", "done": 0, "total": 0, "log": [],
             "error": None, "started": time.time()}
    with JOBS_LOCK:
        JOBS[job_id] = state

    def run():
        try:
            def progress(done, total):
                with JOBS_LOCK:
                    state["done"] = done
                    state["total"] = total
                    if done % 10 == 0 or done == total:
                        state["log"].append(f"probe {done}/{total}")

            result = updates.discover_new(add=add, progress=progress)
            with JOBS_LOCK:
                state["state"] = "done"
                state["result"] = result
                state["done"] = state["total"]
                state["log"].append(
                    f"done: probed {result['probed']} URLs, "
                    f"found {len(result['found'])} online")
        except Exception as e:
            with JOBS_LOCK:
                state["state"] = "error"
                state["error"] = f"{e}\n{traceback.format_exc()}"

    threading.Thread(target=run, daemon=True).start()
    return jsonify({"ok": True, "job": job_id})


@app.post("/api/download")
def api_download():
    data = request.get_json(force=True, silent=True) or {}
    url = data.get("url")
    if not url:
        return jsonify({"ok": False, "error": "provide a url"}), 400
    pkg = next((p for p in updates.load_registry().get("packages", [])
                if p.get("url") == url), None)
    if not pkg:
        return jsonify({"ok": False, "error": "url not in the registry"}), 400
    if updates.local_path(pkg):
        return jsonify({"ok": False, "error": "file already exists in "
                        "downloads/"}), 400
    job_id = uuid.uuid4().hex
    state = {"state": "running", "done": 0, "total": 0, "log": [],
             "error": None, "started": time.time()}
    with JOBS_LOCK:
        JOBS[job_id] = state

    def run():
        try:
            def log(line):
                with JOBS_LOCK:
                    state["log"].append(line)

            def progress(done, total):
                with JOBS_LOCK:
                    state["done"] = done
                    state["total"] = total

            updates.download(url, progress=progress, log=log)
            with JOBS_LOCK:
                state["state"] = "done"
                state["log"].append("download finished in downloads/")
        except Exception as e:
            with JOBS_LOCK:
                state["state"] = "error"
                state["error"] = f"{e}\n{traceback.format_exc()}"

    threading.Thread(target=run, daemon=True).start()
    return jsonify({"ok": True, "job": job_id})


@app.get("/api/sd/status")
def api_sd_status():
    sd = update_sd.detect_sd()
    overall = mapdata.overall_backup_path()
    plan = mapdata.install_plan()
    return jsonify({
        "ok": True,
        "sd": sd,
        "overall_backup": overall,
        "overall_backup_present": bool(overall and os.path.isfile(overall)),
        "install_steps": plan["steps"],
        "manual_steps": plan["manual"],
        "sevenzip": osutil.find_7z(),
        "rsync": shutil.which("rsync") is not None,
    })


@app.get("/api/sd/sources")
def api_sd_sources():
    try:
        sources = update_sd.list_sources(full=True)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({"ok": True, "sources": sources})


@app.get("/api/browse")
def api_browse():
    addr = request.remote_addr or ""
    if addr not in ("127.0.0.1", "::1"):
        return jsonify({"ok": False, "error": "browsing only allowed via "
                                               "localhost"}), 403
    path = _expand(request.args.get("path")) or os.path.expanduser("~")
    path = os.path.abspath(path)
    if not os.path.isdir(path):
        return jsonify({"ok": False, "error": f"not a directory: {path}"}), 400
    entries = []
    try:
        for name in sorted(os.listdir(path)):
            p = os.path.join(path, name)
            if os.path.isdir(p):
                entries.append({"name": name, "path": p,
                                "maps": os.path.isdir(
                                    os.path.join(p, "maps"))})
    except OSError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    parent = os.path.dirname(path)
    drives = None
    if parent == path:
        parent = None
        if os.name == "nt":
            drives = _win_drives()
    return jsonify({"ok": True, "path": path, "parent": parent,
                    "drives": drives, "entries": entries})


def _win_drives() -> list:
    """Detected Windows drive roots (used at the top of the folder browser)."""
    out = []
    for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        p = f"{letter}:\\"
        if os.path.isdir(p):
            out.append({"name": p, "path": p})
    return out


@app.get("/api/profile")
def api_profile():
    overall = mapdata.overall_backup_path()
    car = mapdata.car_profile()
    return jsonify({
        "ok": True,
        "configured": bool(mapdata.config_source()),
        "profile_set": mapdata.profile_set(),
        "config_source": mapdata.config_source(),
        "car": car,
        "overall_backup": overall,
        "overall_backup_present": bool(overall and os.path.isfile(overall)),
        "sources": _profile_sources(),
    })


def _profile_sources() -> list:
    """Candidate folders to detect a car profile from (no typing needed).

    Only things that actually describe the *car* are listed: the detected
    SD card, and backup folders under BACKUP/ that contain a maps/ tree
    (the tool creates these automatically on every SD-card update, so the
    list grows as the user works). Extracted map packages are deliberately
    NOT listed -- they describe the map, not the car.
    """
    out = []
    try:
        sd = update_sd.detect_sd()
        if sd:
            out.append({"label": f"SD card: {update_sd.version_label(sd['info'])}",
                        "path": sd["mount"]})
    except Exception:
        pass
    if os.path.isdir(mapdata.BACKUP_DIR):
        for name in sorted(os.listdir(mapdata.BACKUP_DIR)):
            p = os.path.join(mapdata.BACKUP_DIR, name)
            if (os.path.isdir(p)
                    and os.path.isdir(os.path.join(p, "maps"))):
                out.append({"label": f"Backup: {name}", "path": p})
    return out


@app.post("/api/profile/detect")
def api_profile_detect():
    data = request.get_json(force=True, silent=True) or {}
    path = _expand(data.get("path")) or None
    if not path:
        sd = update_sd.detect_sd()
        if sd:
            path = sd["mount"]
    if not path:
        return jsonify({"ok": False,
                        "error": "no SD card found and no path given; insert "
                                 "the card or enter a backup folder"}), 400
    if not os.path.isdir(path):
        return jsonify({"ok": False,
                        "error": f"not a folder: {path}"}), 400
    try:
        derived = mapdata.derive_profile(path)
    except (mapdata.MapError, OSError) as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    overall_target = None
    if derived.get("overall_src"):
        overall_target = os.path.join(
            mapdata.BACKUP_DIR, "original", "maps", "EEC", "EEC_WLD",
            "OVERALL.NDS")
    return jsonify({"ok": True, "path": path, **derived,
                    "overall_target": overall_target})


@app.post("/api/profile/save")
def api_profile_save():
    data = request.get_json(force=True, silent=True) or {}
    car = data.get("car")
    if not isinstance(car, dict):
        return jsonify({"ok": False, "error": "missing car profile"}), 400
    try:
        result = mapdata.save_profile(car, data.get("overall_src"))
    except mapdata.MapError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except OSError as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({"ok": True, **result})


@app.post("/api/sd/install")
def api_sd_install():
    data = request.get_json(force=True, silent=True) or {}
    source_path = _expand(data.get("source"))
    sd_mount = _expand(data.get("sd")) or None
    dry_run = bool(data.get("dry_run"))
    if not source_path or not os.path.exists(source_path):
        return jsonify({"ok": False, "error": "no valid source path"}), 400
    sd = update_sd.detect_sd(sd_mount)
    if not sd:
        return jsonify({"ok": False,
                        "error": "no MIB2 SD card found (a mount containing "
                                 "maps/00/nds/dbinfo.txt)"}), 400
    weights = {"check": 0, "backup": 35, "extract": 10, "copy": 45,
               "workaround": 5, "verify": 5}
    offsets = {}
    _cum = 0
    for _s in ("check", "backup", "extract", "copy", "workaround", "verify"):
        offsets[_s] = _cum
        _cum += weights[_s]
    job_id = uuid.uuid4().hex
    state = {"state": "running", "done": 0, "total": 100, "log": [],
             "error": None, "phase": None, "eta": None, "started": time.time()}
    with JOBS_LOCK:
        JOBS[job_id] = state

    def run():
        try:
            def log(line):
                with JOBS_LOCK:
                    state["log"].append(line)

            def progress(stage, pct):
                with JOBS_LOCK:
                    now = time.time()
                    if stage != state.get("phase"):
                        state["stage_start"] = now
                        state["stage_max"] = 0
                    state["phase"] = stage
                    state["stage_max"] = max(state.get("stage_max", 0), pct)
                    state["done"] = offsets.get(stage, 0) + int(
                        weights.get(stage, 0) * state["stage_max"] / 100)
                    frac = state["stage_max"] / 100.0
                    elapsed = now - state.get("stage_start", now)
                    eta = None
                    if frac >= 0.1:
                        eta = int(max(0, elapsed / frac - elapsed))
                    state["eta"] = eta

            result = update_sd.install_to_sd(
                sd["mount"], source_path, progress=progress, log=log,
                dry_run=dry_run)
            with JOBS_LOCK:
                state["state"] = "done"
                state["result"] = result
                state["done"] = 100
                state["phase"] = "done"
        except Exception as e:
            with JOBS_LOCK:
                state["state"] = "error"
                state["error"] = f"{e}\n{traceback.format_exc()}"

    threading.Thread(target=run, daemon=True).start()
    return jsonify({"ok": True, "job": job_id, "sd": sd})


@app.post("/api/verify-md5")
def api_verify_md5():
    data = request.get_json(force=True, silent=True) or {}
    path = _expand(data.get("path"))
    try:
        source = mapdata.resolve_source(path)
    except mapdata.MapError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    manifest = source.md5_manifest()
    if not manifest:
        return jsonify({"ok": False, "error": "md5 check only works for "
                        "extracted folders with a .md5sum.txt in the "
                        "package folder"}), 400
    job_id = uuid.uuid4().hex
    state = {"state": "running", "done": 0, "total": 0, "log": [],
             "error": None, "manifest": os.path.basename(manifest)}
    with JOBS_LOCK:
        JOBS[job_id] = state

    def run():
        try:
            proc = subprocess.Popen(
                ["md5sum", "-c", manifest],
                cwd=source.maps_root,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1)
            ok = n = 0
            for line in proc.stdout:
                line = line.rstrip()
                with JOBS_LOCK:
                    state["log"].append(line)
                if line.endswith(": OK"):
                    ok += 1
                n += 1
            proc.wait()
            with JOBS_LOCK:
                state["state"] = "done"
                state["ok_count"] = ok
                state["total"] = n
        except Exception as e:
            with JOBS_LOCK:
                state["state"] = "error"
                state["error"] = str(e)

    threading.Thread(target=run, daemon=True).start()
    return jsonify({"ok": True, "job": job_id,
                    "manifest": os.path.basename(manifest)})


@app.get("/api/search")
def api_search():
    path = _expand(request.args.get("path"))
    q = (request.args.get("q") or "").strip()
    mode = request.args.get("mode", "contains")
    regions = [int(x) for x in request.args.get("regions", "").split(",")
               if x.strip()]
    if not q:
        return jsonify({"ok": True, "total": 0, "results": []})
    try:
        m = _load_map(path)
    except mapdata.MapError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    res = mapdata.search(m, q, mode=mode, region_filter=regions or None)
    return jsonify({"ok": True, **res})


@app.get("/api/coverage")
def api_coverage():
    path = _expand(request.args.get("path"))
    try:
        m = _load_map(path)
    except mapdata.MapError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    meta = mapdata.meta_dir(m.source)
    png = os.path.join(meta, "coverage.png")
    stats_path = os.path.join(meta, "coverage.json")
    force = request.args.get("force") == "1"
    if force or not os.path.exists(png) or not os.path.exists(stats_path):
        try:
            stats = mapdata.render_coverage(
                m, png, dpi=int(request.args.get("dpi", 130)),
                ne_path=mapdata.DEFAULT_NE)
        except Exception as e:
            return jsonify({"ok": False, "error": f"failed to render the "
                            f"map: {e}\n{traceback.format_exc()}"}), 500
        with open(stats_path, "w") as fh:
            json.dump(stats, fh, ensure_ascii=False)
    else:
        with open(stats_path) as fh:
            stats = json.load(fh)
    return jsonify({"ok": True, "stats": stats,
                    "png": "/api/coverage.png?path=" +
                           request.args.get("path", "")})


@app.get("/api/coverage.png")
def api_coverage_png():
    path = _expand(request.args.get("path"))
    try:
        m = _load_map(path)
    except mapdata.MapError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    meta = mapdata.meta_dir(m.source)
    png = os.path.join(meta, "coverage_ax.png")
    if not os.path.exists(png):
        png = os.path.join(meta, "coverage.png")
    if not os.path.exists(png):
        return jsonify({"ok": False, "error": "map not generated yet"}), 404
    return send_file(png, mimetype="image/png")


@app.post("/api/shutdown")
def api_shutdown():
    addr = request.remote_addr or ""
    if addr not in ("127.0.0.1", "::1"):
        return jsonify({"ok": False,
                        "error": "shutdown only allowed via localhost"}), 403
    if _SERVER is None:
        return jsonify({"ok": False,
                        "error": "the server cannot be stopped from here"}), 400

    def _stop():
        time.sleep(0.3)
        _SERVER.shutdown()
        _SERVER.server_close()

    threading.Thread(target=_stop, daemon=True).start()
    return jsonify({"ok": True})


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5000)
    ap.add_argument("--no-browser", action="store_true",
                    help="do not auto-open a browser")
    args = ap.parse_args()
    url = f"http://{args.host}:{args.port}"
    if not args.no_browser:
        threading.Timer(0.7, lambda: webbrowser.open(url)).start()
    print(f"mapui: open {url}")
    global _SERVER
    server = make_server(host=args.host, port=args.port, app=app, threaded=True)
    _SERVER = server
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        _SERVER = None
        server.server_close()


if __name__ == "__main__":
    main()
