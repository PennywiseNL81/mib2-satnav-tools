#!/usr/bin/env python3
"""update_sd.py -- update the MIB2 satnav SD card via a cardreader.

Flow (mirrors the install checklist from mapdata.install_plan()):
  1. detect the SD card by content (a mount with maps/00/nds/dbinfo.txt),
  2. back up the full card to <backup>/SDcard_<date>/ (rsync or a
     pure-Python copy fallback),
  3. extract the chosen package (7z -> a temp folder), clear the card's
     maps/,
  4. copy the new maps/ to the card root,
  5. optionally replace maps/EEC/EEC_WLD/OVERALL.NDS with the original
     (only when the profile enables car.workaround),
  6. verify checksums and print the remaining manual steps.

The card is never reformatted and no block device is ever written to;
only the mounted filesystem is touched. The package must be a local file
in downloads/ (or an extracted folder) -- nothing is downloaded here.

Usage:
    update_sd.py detect                  # show detected card + version
    update_sd.py list                    # show installable packages
    update_sd.py install --source <pkg> [--sd <mount>] [--backup-dir <dir>]
                          [--yes] [--dry-run]
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import os
import shutil
import sys
import tempfile
import time

TOOL_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(TOOL_DIR), "mib2nds-tool"))

import mapdata  # noqa: E402
import osutil  # noqa: E402

EXTRACT_ROOT = os.path.join(tempfile.gettempdir(), "sd-updater")

MAX_BACKUPS = 3


class SDError(Exception):
    pass


def read_dbinfo(path: str) -> dict:
    info = {}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if "=" in line:
                    k, _, v = line.partition("=")
                    info[k.strip()] = v.strip().strip('"')
    except OSError:
        pass
    return info


def detect_sd(override: str = None) -> dict:
    """Return the MIB2 SD card mount, or None. Detected purely by content."""
    cands = list(osutil.sd_mounts())
    if override:
        cands = [("/dev/override", os.path.abspath(override))]
    for dev, mnt in cands:
        if mnt in ("/", "/home", "/boot", "/usr", "/var"):
            continue
        db = os.path.join(mnt, "maps", "00", "nds", "dbinfo.txt")
        if os.path.isfile(db):
            return {"device": dev, "mount": mnt,
                    "info": read_dbinfo(db),
                    "usage": _usage(mnt)}
    return None


def _usage(mnt: str) -> dict:
    try:
        u = shutil.disk_usage(mnt)
        return {"total": u.total, "used": u.used, "free": u.free}
    except OSError:
        return None


def _fmt_bytes(n) -> str:
    if n is None:
        return "?"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n:.1f} TB"


def version_label(info: dict) -> str:
    sysname = info.get("SystemName", "?")
    ver = info.get("ApplicationSoftwareVersionNumber", "?")
    part = info.get("PartNumber2", "?")
    return f"{sysname} v{ver} (part {part})"


def list_sources(full: bool = True) -> list:
    out = []
    for s in mapdata.find_sources():
        entry = {"label": s["label"], "path": s["path"], "kind": s["kind"]}
        try:
            src = mapdata.resolve_source(s["path"])
            val = mapdata.validate(src)
            entry["ok"] = val["ok"]
            entry["system"] = val["info"].get("SystemName", "?")
            entry["version"] = val["info"].get(
                "ApplicationSoftwareVersionNumber", "?")
            entry["size"] = src.size_bytes()
            entry["error"] = "; ".join(val["errors"]) if not val["ok"] else None
            if full:
                compat = mapdata.compatibility_check(src)
                entry["verdict"] = compat["verdict"]
                entry["missing"] = compat.get("missing", [])
        except Exception as e:
            entry["ok"] = False
            entry["error"] = str(e)
        out.append(entry)
    return out


def _run(cmd, **kw):
    try:
        osutil.run_cmd(cmd, **kw)
    except osutil.CommandError as e:
        raise SDError(str(e)) from e


def _copy_dir(src: str, dst: str, **kw) -> None:
    try:
        osutil.copy_tree(src, dst, **kw)
    except osutil.CommandError as e:
        raise SDError(str(e)) from e


def install_to_sd(sd_mount: str, source_path: str, backup_dir: str = None,
                  keep_backups: int = MAX_BACKUPS,
                  progress=None, log=None, dry_run: bool = False) -> dict:
    """Run the full backup + install flow on the mounted card.

    progress(stage, pct) receives stage in
    ("check", "backup", "extract", "copy", "workaround", "verify", "done").
    """
    def emit(stage, pct):
        if progress:
            progress(stage, pct)

    def logmsg(msg):
        if log:
            log(msg)

    # ---- 0. source + compat ----
    emit("check", 0)
    if not os.path.isdir(sd_mount):
        raise SDError(f"not an SD mount: {sd_mount}")
    source = mapdata.resolve_source(source_path)
    val = mapdata.validate(source)
    if not val["ok"]:
        raise SDError("source is not a valid MIB2 map: "
                      + "; ".join(val["errors"]))
    logmsg(f"source: {source.name} ({source.kind}) "
           f"{val['info'].get('SystemName', '?')} "
           f"v{val['info'].get('ApplicationSoftwareVersionNumber', '?')}")
    compat = mapdata.compatibility_check(source)
    logmsg(f"compatibility: {compat['verdict']}")
    if not compat["verdict_ok"]:
        raise SDError(f"compatibility check failed ({compat['verdict']}); "
                      "install refused. Use a suitable package.")
    size = source.size_bytes()

    # ---- 1. workaround source (only when the profile enables it) ----
    overall_orig = mapdata.overall_backup_path()
    workaround = bool(overall_orig)
    if workaround and not os.path.isfile(overall_orig):
        raise SDError("original OVERALL.NDS is missing; install refused "
                      "(the configured OVERALL.NDS workaround cannot run: "
                      f"{overall_orig}).")
    if not workaround:
        logmsg("no OVERALL.NDS workaround configured (car.workaround) - "
               "skipping")
    overall_dst = os.path.join(sd_mount, "maps", "EEC", "EEC_WLD",
                               "OVERALL.NDS")

    # ---- 1b. preflight: SD card writable? ----
    if not dry_run:
        probe = os.path.join(sd_mount, ".sd_write_test")
        try:
            with open(probe, "wb") as fh:
                fh.write(b"x")
            os.remove(probe)
        except OSError as e:
            raise SDError(
                "SD card is write-protected (read-only). Check the lock "
                "slider on the SD adapter and re-insert the card."
            ) from e
        logmsg("SD card is writable")

    # ---- 2. backup ----
    if not backup_dir:
        backup_dir = os.path.join(
            mapdata.BACKUP_DIR, "SDcard_" + time.strftime("%Y%m%d_%H%M%S"))
    if not dry_run:
        os.makedirs(backup_dir, exist_ok=True)
    used_on_sd = mapdata._dir_size(sd_mount)
    backup_usage = shutil.disk_usage(mapdata.BACKUP_DIR)
    if backup_usage.free < used_on_sd * 1.02:
        raise SDError(f"insufficient free space in BACKUP/ for the SD "
                      f"backup ({_fmt_bytes(used_on_sd)} needed, "
                      f"{_fmt_bytes(backup_usage.free)} free).")
    logmsg(f"backup: {sd_mount} -> {backup_dir} "
           f"({_fmt_bytes(used_on_sd)})")
    emit("backup", 0)
    if not dry_run:
        _copy_dir(sd_mount, backup_dir, progress_cb=emit_pct("backup", emit),
                  log=logmsg)
    emit("backup", 100)
    if dry_run:
        logmsg("(dry-run: backup skipped)")
    else:
        _rotate_backups(keep=keep_backups, log=logmsg)

    # ---- 3. extract ----
    extract_dir = os.path.join(EXTRACT_ROOT, mapdata._safe_name(source.name))
    if os.path.isdir(extract_dir):
        shutil.rmtree(extract_dir)
    try:
        os.makedirs(extract_dir, exist_ok=True)
        emit("extract", 0)
        if not dry_run and source.kind in ("zip", "7z"):
            logmsg(f"extracting to {extract_dir}")
            sevenzip = osutil.find_7z() or "7z"
            _run([sevenzip, "x", source_path, f"-o{extract_dir}", "-y"],
                 pct_parser=osutil.sevenzip_pct,
                 progress_cb=emit_pct("extract", emit), log=logmsg)
        emit("extract", 100)

        if source.kind == "folder":
            src_maps = os.path.join(source_path, "maps")
            if not os.path.isdir(src_maps):
                src_maps = source_path
        else:
            src_maps = os.path.join(extract_dir, "maps")
            if not dry_run and not os.path.isdir(src_maps):
                raise SDError(f"no maps/ found in extracted package: "
                              f"{extract_dir}")

        # ---- 4. free space check + clear maps/ ----
        emit("copy", 0)
        sd_maps = os.path.join(sd_mount, "maps")
        old_maps = mapdata._dir_size(sd_maps) if os.path.isdir(sd_maps) else 0
        usage = shutil.disk_usage(sd_mount)
        if usage.free + old_maps < size * 1.05:
            raise SDError(f"insufficient free space on the SD card "
                          f"({_fmt_bytes(usage.free)} free, "
                          f"+{_fmt_bytes(old_maps)} after clearing maps/, "
                          f"~{_fmt_bytes(int(size * 1.05))} needed).")
        logmsg(f"clearing maps/ on SD: {sd_maps}")
        if not dry_run and os.path.isdir(sd_maps):
            shutil.rmtree(sd_maps)
        if not dry_run:
            for e in os.listdir(sd_mount):
                if e.lower().endswith(".md5sum.txt"):
                    os.remove(os.path.join(sd_mount, e))
                    logmsg(f"removed old manifest: {e}")
        logmsg(f"copying maps/ -> {sd_mount}/")
        if not dry_run:
            _copy_dir(src_maps, sd_maps, progress_cb=emit_pct("copy", emit),
                      log=logmsg)
        emit("copy", 100)

        # ---- 5. workaround (optional) ----
        emit("workaround", 0)
        if workaround:
            if not dry_run:
                os.makedirs(os.path.dirname(overall_dst), exist_ok=True)
                shutil.copy2(overall_orig, overall_dst)
                logmsg(f"OVERALL.NDS replaced with the original: {overall_dst}")
            else:
                logmsg(f"(dry-run: OVERALL.NDS would be replaced with "
                       f"{overall_orig})")
        emit("workaround", 100)

        # ---- 6. verify ----
        emit("verify", 0)
        if dry_run:
            emit("verify", 100)
            return _summary(sd_mount, source, backup_dir, dry_run=True)
        logmsg("verification (checksum comparison)...")
        diff = osutil.verify_tree(
            src_maps, sd_maps,
            exclude_rel="EEC/EEC_WLD/OVERALL.NDS" if workaround else None)
        if diff:
            raise SDError("verification failed: files on the SD card "
                          "differ:\n" + "\n".join(diff[:20]))
        if workaround:
            def md5(p):
                h = hashlib.md5()
                with open(p, "rb") as fh:
                    for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                        h.update(chunk)
                return h.hexdigest()
            if md5(overall_orig) != md5(overall_dst):
                raise SDError("verification failed: OVERALL.NDS on the SD "
                              "card differs from the original.")
        emit("verify", 100)
        return _summary(sd_mount, source, backup_dir, dry_run=False)
    finally:
        if os.path.isdir(extract_dir):
            shutil.rmtree(extract_dir, ignore_errors=True)
        logmsg(f"temporary extraction folder removed: {extract_dir}")


def emit_pct(stage, emit):
    seen = 0

    def cb(pct):
        nonlocal seen
        seen = max(seen, max(0, min(100, pct)))
        emit(stage, seen)

    return cb


def _rotate_backups(keep: int = MAX_BACKUPS, log=None) -> list:
    """Keep at most `keep` SDcard_<date> backups in BACKUP/.

    Only directories matching the SDcard_* pattern are touched; other
    content in the backup dir (original-card copies, .img images) is never
    affected. The oldest backups are removed first, so the most recent
    (incl. the directly preceding card state) always survive.
    """
    pattern = os.path.join(mapdata.BACKUP_DIR, "SDcard_*")
    existing = sorted(
        (p for p in glob.glob(pattern) if os.path.isdir(p)),
        key=lambda p: os.path.basename(p),
    )
    removed = []
    while len(existing) > keep:
        oldest = existing.pop(0)
        if log:
            log(f"backup rotation: removing oldest backup: {oldest}")
        shutil.rmtree(oldest, ignore_errors=True)
        removed.append(oldest)
    return removed


def _summary(sd_mount, source, backup_dir, dry_run: bool) -> dict:
    plan = mapdata.install_plan()
    overall = mapdata.overall_backup_path()
    return {
        "done": True,
        "dry_run": dry_run,
        "source": source.name,
        "backup_dir": backup_dir,
        "sd_mount": sd_mount,
        "overall_backup": overall,
        "manual_steps": plan["manual"],
        "manual_steps_all": plan["steps"],
        "lock_note": ("If your SD adapter has a lock slider, move it back to "
                      "'read' (unlocked) before inserting the card into the "
                      "car; the MIB2 unit must be able to write to the card "
                      "during the update."),
    }


def cmd_detect(_args) -> int:
    sd = detect_sd()
    if not sd:
        print("no MIB2 SD card found (a mount containing "
              "maps/00/nds/dbinfo.txt).")
        print("Insert the card into the cardreader and mount the partition.")
        return 1
    print(f"SD card found:")
    print(f"  device:  {sd['device']}")
    print(f"  mount:   {sd['mount']}")
    print(f"  version: {version_label(sd['info'])}")
    if sd.get("usage"):
        u = sd["usage"]
        print(f"  free:    {_fmt_bytes(u['free'])}")
    return 0


def cmd_list(_args) -> int:
    sources = list_sources()
    if not sources:
        print("no packages found in downloads/ or the project root.")
        return 1
    print("Available packages:")
    for i, s in enumerate(sources, 1):
        verdict = s.get("verdict") or "not checked"
        size = _fmt_bytes(s.get("size")) if s.get("size") else "?"
        print(f"  {i:2}. {s['label']} ({s['kind']}) - {size} - {verdict}")
        if s.get("error"):
            print(f"      error: {s['error']}")
    return 0


def cmd_install(args) -> int:
    if args.source:
        source_path = os.path.abspath(os.path.expanduser(args.source))
        if not os.path.exists(source_path):
            print(f"error: path does not exist: {source_path}")
            return 1
    else:
        sources = list_sources()
        if not sources:
            print("no packages found; use --source <path>.")
            return 1
        print("Choose a package:")
        for i, s in enumerate(sources, 1):
            print(f"  {i:2}. {s['label']} ({s['kind']})")
        try:
            idx = int(input("number: ").strip())
            source_path = sources[idx - 1]["path"]
        except (ValueError, IndexError):
            print("invalid number.")
            return 1
    sd = detect_sd(args.sd)
    if not sd:
        print("no MIB2 SD card found; insert the card into the cardreader "
              "or pass --sd <mount>.")
        return 1
    mnt = sd["mount"]
    info = sd["info"]
    print(f"SD card:  {mnt} ({version_label(info)})")
    print(f"Source:   {source_path}")
    if not args.dry_run and not args.yes:
        print("\nThis will replace ALL existing map data on the SD card.")
        ans = input("continue? [y/N] ").strip().lower()
        if ans not in ("y", "yes"):
            print("cancelled.")
            return 1

    def on_progress(stage, pct):
        if stage == "done":
            print("[100%] done")
            return
        print(f"  {stage}: {pct:3d}%")

    def on_log(line):
        print("  | " + line)

    try:
        result = install_to_sd(mnt, source_path, backup_dir=args.backup_dir,
                               keep_backups=args.keep_backups,
                               progress=on_progress, log=on_log,
                               dry_run=args.dry_run)
    except (SDError, osutil.CommandError, OSError) as e:
        print(f"\nERROR: {e}")
        return 1

    print("\n=== Update complete ===")
    print(f"source:   {result['source']}")
    print(f"backup:   {result['backup_dir']}")
    print(f"workaround: {result['overall_backup']} -> "
          "SD/maps/EEC/EEC_WLD/OVERALL.NDS")
    print("\nManual steps, after this copy:")
    for i, s in enumerate(result["manual_steps"], 1):
        print(f"  {len(result['manual_steps_all']) - len(result['manual_steps']) + i}. {s}")
    if not result["dry_run"]:
        print(f"\n  ! {result['lock_note']}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("detect", help="show the detected SD card")
    sub.add_parser("list", help="list available packages")
    p_install = sub.add_parser("install", help="back up and update the SD card")
    p_install.add_argument("--source", help="path to the package (.zip/.7z/folder)")
    p_install.add_argument("--sd", help="mount path override (for tests)")
    p_install.add_argument("--backup-dir", help="backup location (default: "
                           "BACKUP/SDcard_<date>)")
    p_install.add_argument("--keep-backups", type=int, default=MAX_BACKUPS,
                           metavar="N",
                           help="maximum number of SDcard backups to keep; "
                                f"the oldest are removed (default: "
                                f"{MAX_BACKUPS})")
    p_install.add_argument("--yes", action="store_true",
                           help="do not ask for confirmation")
    p_install.add_argument("--dry-run", action="store_true",
                           help="run through all steps without writing")
    args = ap.parse_args(argv)
    if not args.cmd:
        ap.print_help()
        return 0
    if args.cmd == "detect":
        return cmd_detect(args)
    if args.cmd == "list":
        return cmd_list(args)
    if args.cmd == "install":
        return cmd_install(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
