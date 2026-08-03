#!/usr/bin/env python3
"""update_sd.py -- update the MIB2 satnav SD card via a cardreader.

Flow (mirrors the install checklist from mapdata.install_plan()):
  1. detect the SD card by content (a mount with maps/00/nds/dbinfo.txt),
  2. back up the full card to <backup>/SDcard_<datum>/ (rsync),
  3. extract the chosen package (7z -> /tmp), clear the card's maps/,
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
import os
import pty
import re
import shutil
import subprocess
import sys
import time

TOOL_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(TOOL_DIR), "mib2nds-tool"))

import mapdata  # noqa: E402

EXTRACT_ROOT = "/tmp/sd-updater"

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


def _mounts() -> list:
    out = []
    try:
        with open("/proc/mounts") as fh:
            for line in fh:
                parts = line.split()
                if len(parts) >= 2:
                    out.append((parts[0], parts[1]))
    except OSError:
        pass
    return out


def detect_sd(override: str = None) -> dict:
    """Return the MIB2 SD card mount, or None. Detected purely by content."""
    cands = [(d, m) for d, m in _mounts()]
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


def _iter_proc(stream):
    """Iterate a byte stream's lines, splitting on both \\n and \\r so that
    `rsync --info=progress2` and 7z progress (carriage-return based) parse."""
    buf = b""
    while True:
        try:
            chunk = stream.read(65536)
        except OSError:
            break
        if not chunk:
            break
        buf += chunk
        parts = re.split(rb"[\r\n\x08]+", buf)
        buf = parts.pop()
        for p in parts:
            yield p.decode("utf-8", "replace")
    if buf:
        yield buf.decode("utf-8", "replace")


def _rsync_pct(line: str):
    m = re.match(r"^\s*[\d.,]+\s+(\d+)%", line)
    return int(m.group(1)) if m else None


def _sevenzip_pct(line: str):
    m = re.match(r"^\s*(\d+)%(?:\s|$)", line)
    return int(m.group(1)) if m else None


def _run(cmd, cwd=None, pct_parser=None, progress_cb=None, log=None):
    want_progress = pct_parser is not None and progress_cb is not None
    stream = None
    proc = None
    if want_progress:
        master = slave = None
        try:
            master, slave = pty.openpty()
            proc = subprocess.Popen(
                cmd, cwd=cwd, stdout=slave, stderr=slave,
                stdin=subprocess.DEVNULL, close_fds=True,
                env=dict(os.environ, LANG="C", LC_ALL="C"))
            os.close(slave)
            stream = os.fdopen(master, "rb", buffering=0)
        except OSError:
            if slave is not None:
                try:
                    os.close(slave)
                except OSError:
                    pass
            if master is not None:
                try:
                    os.close(master)
                except OSError:
                    pass
            proc = None
    if proc is None:
        proc = subprocess.Popen(
            cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            env=dict(os.environ, LANG="C", LC_ALL="C"))
        stream = proc.stdout
    try:
        for line in _iter_proc(stream):
            if log:
                log(line)
            if pct_parser and progress_cb:
                pct = pct_parser(line)
                if pct is not None:
                    progress_cb(pct)
    finally:
        try:
            stream.close()
        except OSError:
            pass
    proc.wait()
    if proc.returncode != 0:
        raise SDError(f"commando mislukt (code {proc.returncode}): "
                      f"{' '.join(cmd)}")
    if want_progress and progress_cb:
        progress_cb(100)


def _copy_dir(src: str, dst: str, progress_cb=None, log=None) -> None:
    os.makedirs(dst, exist_ok=True)
    _run(["rsync", "-a", "--info=progress2",
          os.path.join(src, "") or src, os.path.join(dst, "") or dst],
         pct_parser=_rsync_pct, progress_cb=progress_cb, log=log)


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
        raise SDError(f"geen SD-mount: {sd_mount}")
    source = mapdata.resolve_source(source_path)
    val = mapdata.validate(source)
    if not val["ok"]:
        raise SDError("bron is geen geldige MIB2-kaart: "
                      + "; ".join(val["errors"]))
    logmsg(f"bron: {source.name} ({source.kind}) "
           f"{val['info'].get('SystemName', '?')} "
           f"v{val['info'].get('ApplicationSoftwareVersionNumber', '?')}")
    compat = mapdata.compatibility_check(source)
    logmsg(f"compatibiliteit: {compat['verdict']}")
    if not compat["verdict_ok"]:
        raise SDError(f"compatibiliteitscheck faalt ({compat['verdict']}); "
                      "installatie geweigerd. Gebruik een geschikt pakket.")
    size = source.size_bytes()

    # ---- 1. workaround source (only when the profile enables it) ----
    overall_orig = mapdata.overall_backup_path()
    workaround = bool(overall_orig)
    if workaround and not os.path.isfile(overall_orig):
        raise SDError("originele OVERALL.NDS ontbreekt; installatie geweigerd "
                      "(de geconfigureerde OVERALL.NDS-workaround kan niet "
                      f"worden uitgevoerd: {overall_orig}).")
    if not workaround:
        logmsg("geen OVERALL.NDS-workaround geconfigureerd (car.workaround) - "
               "overslaan")
    overall_dst = os.path.join(sd_mount, "maps", "EEC", "EEC_WLD",
                               "OVERALL.NDS")

    # ---- 1b. preflight: SD-kaart beschrijfbaar? ----
    if not dry_run:
        probe = os.path.join(sd_mount, ".sd_write_test")
        try:
            with open(probe, "wb") as fh:
                fh.write(b"x")
            os.remove(probe)
        except OSError as e:
            raise SDError(
                "SD-kaart is write-protected (alleen-lezen). Controleer het "
                "lock-schuifje op de SD-adapter en steek de kaart opnieuw "
                "in."
            ) from e
        logmsg("SD-kaart is beschrijfbaar")

    # ---- 2. backup ----
    if not backup_dir:
        backup_dir = os.path.join(
            mapdata.BACKUP_DIR, "SDcard_" + time.strftime("%Y%m%d_%H%M%S"))
    if not dry_run:
        os.makedirs(backup_dir, exist_ok=True)
    used_on_sd = mapdata._dir_size(sd_mount)
    backup_usage = shutil.disk_usage(mapdata.BACKUP_DIR)
    if backup_usage.free < used_on_sd * 1.02:
        raise SDError(f"onvoldoende vrije ruimte in BACKUP/ voor de "
                      f"SD-backup ({_fmt_bytes(used_on_sd)} nodig, "
                      f"{_fmt_bytes(backup_usage.free)} vrij).")
    logmsg(f"backup: {sd_mount} -> {backup_dir} "
           f"({_fmt_bytes(used_on_sd)})")
    emit("backup", 0)
    if not dry_run:
        _copy_dir(sd_mount, backup_dir, progress_cb=emit_pct("backup", emit),
                  log=logmsg)
    emit("backup", 100)
    if dry_run:
        logmsg("(dry-run: backup overgeslagen)")
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
            logmsg(f"uitpakken naar {extract_dir}")
            sevenzip = mapdata._find_7z() or "7z"
            _run([sevenzip, "x", source_path, f"-o{extract_dir}", "-y"],
                 pct_parser=_sevenzip_pct,
                 progress_cb=emit_pct("extract", emit), log=logmsg)
        emit("extract", 100)

        if source.kind == "folder":
            src_maps = os.path.join(source_path, "maps")
            if not os.path.isdir(src_maps):
                src_maps = source_path
        else:
            src_maps = os.path.join(extract_dir, "maps")
            if not dry_run and not os.path.isdir(src_maps):
                raise SDError(f"geen maps/ gevonden in uitgepakt pakket: "
                              f"{extract_dir}")

        # ---- 4. free space check + clear maps/ ----
        emit("copy", 0)
        sd_maps = os.path.join(sd_mount, "maps")
        old_maps = mapdata._dir_size(sd_maps) if os.path.isdir(sd_maps) else 0
        usage = shutil.disk_usage(sd_mount)
        if usage.free + old_maps < size * 1.05:
            raise SDError(f"onvoldoende vrije ruimte op de SD-kaart "
                          f"({_fmt_bytes(usage.free)} vrij, "
                          f"+{_fmt_bytes(old_maps)} na wissen van maps/, "
                          f"nodig ~{_fmt_bytes(int(size * 1.05))}).")
        logmsg(f"maps/ op SD wissen: {sd_maps}")
        if not dry_run and os.path.isdir(sd_maps):
            shutil.rmtree(sd_maps)
        if not dry_run:
            for e in os.listdir(sd_mount):
                if e.lower().endswith(".md5sum.txt"):
                    os.remove(os.path.join(sd_mount, e))
                    logmsg(f"oud manifest verwijderd: {e}")
        logmsg(f"kopieer maps/ -> {sd_mount}/")
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
                logmsg(f"OVERALL.NDS vervangen door origineel: {overall_dst}")
            else:
                logmsg(f"(dry-run: OVERALL.NDS zou worden vervangen door "
                       f"{overall_orig})")
        emit("workaround", 100)

        # ---- 6. verify ----
        emit("verify", 0)
        if dry_run:
            emit("verify", 100)
            return _summary(sd_mount, source, backup_dir, dry_run=True)
        logmsg("verificatie (checksum-vergelijking)...")
        rsync_excl = ["--exclude=EEC/EEC_WLD/OVERALL.NDS"] if workaround else []
        proc = subprocess.run(
            ["rsync", "-rcn",
             *rsync_excl,
             "--out-format=%n",
             os.path.join(src_maps, "") or src_maps,
             os.path.join(sd_maps, "") or sd_maps],
            capture_output=True, text=True,
            env=dict(os.environ, LANG="C", LC_ALL="C"))
        diff = [l for l in proc.stdout.splitlines() if l.strip()]
        if diff:
            raise SDError("verificatie mislukt: bestanden op SD wijken af:\n"
                          + "\n".join(diff[:20]))
        if workaround:
            import hashlib
            def md5(p):
                h = hashlib.md5()
                with open(p, "rb") as fh:
                    for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                        h.update(chunk)
                return h.hexdigest()
            if md5(overall_orig) != md5(overall_dst):
                raise SDError("verificatie mislukt: OVERALL.NDS op de SD "
                              "wijkt af van het origineel.")
        emit("verify", 100)
        return _summary(sd_mount, source, backup_dir, dry_run=False)
    finally:
        if os.path.isdir(extract_dir):
            shutil.rmtree(extract_dir, ignore_errors=True)
        logmsg(f"tijdelijke uitpakmap opgeruimd: {extract_dir}")


def emit_pct(stage, emit):
    seen = 0
    def cb(pct):
        nonlocal seen
        seen = max(seen, max(0, min(100, pct)))
        emit(stage, seen)
    return cb


def _rotate_backups(keep: int = MAX_BACKUPS, log=None) -> list:
    """Keep at most `keep` SDcard_<datum> backups in BACKUP/.

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
            log(f"backup-rotatie: oudste backup verwijderen: {oldest}")
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
        "lock_note": ("Zet, als je SD-adapter een lock-schuifje heeft, het "
                      "schuifje terug op 'read' (ontgrendeld) vóórdat je de "
                      "kaart in de auto steekt; de MIB2-unit moet tijdens de "
                      "update op de kaart kunnen schrijven."),
    }


def cmd_detect(_args) -> int:
    sd = detect_sd()
    if not sd:
        print("geen MIB2-SD-kaart gevonden (mount met maps/00/nds/dbinfo.txt).")
        print("Steek de kaart in de cardreader en mount de partitie.")
        return 1
    print(f"SD-kaart gevonden:")
    print(f"  apparaat: {sd['device']}")
    print(f"  mount:    {sd['mount']}")
    print(f"  versie:   {version_label(sd['info'])}")
    if sd.get("usage"):
        u = sd["usage"]
        print(f"  ruimte:   {_fmt_bytes(u['free'])} vrij")
    return 0


def cmd_list(_args) -> int:
    sources = list_sources()
    if not sources:
        print("geen pakketten gevonden in downloads/ of projectroot.")
        return 1
    print("Beschikbare pakketten:")
    for i, s in enumerate(sources, 1):
        verdict = s.get("verdict") or "niet gecontroleerd"
        size = _fmt_bytes(s.get("size")) if s.get("size") else "?"
        print(f"  {i:2}. {s['label']} ({s['kind']}) - {size} - {verdict}")
        if s.get("error"):
            print(f"      fout: {s['error']}")
    return 0


def cmd_install(args) -> int:
    if args.source:
        source_path = os.path.abspath(os.path.expanduser(args.source))
        if not os.path.exists(source_path):
            print(f"fout: pad bestaat niet: {source_path}")
            return 1
    else:
        sources = list_sources()
        if not sources:
            print("geen pakketten gevonden; gebruik --source <pad>.")
            return 1
        print("Kies een pakket:")
        for i, s in enumerate(sources, 1):
            print(f"  {i:2}. {s['label']} ({s['kind']})")
        try:
            idx = int(input("nummer: ").strip())
            source_path = sources[idx - 1]["path"]
        except (ValueError, IndexError):
            print("ongeldig nummer.")
            return 1
    sd = detect_sd(args.sd)
    if not sd:
        print("geen MIB2-SD-kaart gevonden; steek de kaart in de "
              "cardreader of geef --sd <mount>.")
        return 1
    mnt = sd["mount"]
    info = sd["info"]
    print(f"SD-kaart: {mnt} ({version_label(info)})")
    print(f"Bron:     {source_path}")
    if not args.dry_run and not args.yes:
        print("\nDit gaat ALLE bestaande kaartdata op de SD vervangen.")
        ans = input("doorgaan? [y/N] ").strip().lower()
        if ans not in ("y", "yes"):
            print("geannuleerd.")
            return 1

    def on_progress(stage, pct):
        if stage == "done":
            print("[100%] klaar")
            return
        print(f"  {stage}: {pct:3d}%")

    def on_log(line):
        print("  | " + line)

    try:
        result = install_to_sd(mnt, source_path, backup_dir=args.backup_dir,
                               keep_backups=args.keep_backups,
                               progress=on_progress, log=on_log,
                               dry_run=args.dry_run)
    except SDError as e:
        print(f"\nFOUT: {e}")
        return 1

    print("\n=== Update voltooid ===")
    print(f"bron:     {result['source']}")
    print(f"backup:   {result['backup_dir']}")
    print(f"workaround: {result['overall_backup']} -> "
          "SD/maps/EEC/EEC_WLD/OVERALL.NDS")
    print("\nHandmatige stappen, ná deze kopie:")
    for i, s in enumerate(result["manual_steps"], 1):
        print(f"  {len(result['manual_steps_all']) - len(result['manual_steps']) + i}. {s}")
    if not result["dry_run"]:
        print(f"\n  ! {result['lock_note']}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("detect", help="toon gedetecteerde SD-kaart")
    sub.add_parser("list", help="toon beschikbare pakketten")
    p_install = sub.add_parser("install", help="backup + update de SD-kaart")
    p_install.add_argument("--source", help="pad naar pakket (.zip/.7z/folder)")
    p_install.add_argument("--sd", help="mountpad override (voor tests)")
    p_install.add_argument("--backup-dir", help="backuplocatie (default: "
                           "BACKUP/SDcard_<datum>)")
    p_install.add_argument("--keep-backups", type=int, default=MAX_BACKUPS,
                           metavar="N",
                           help="maximaal aantal SDcard-backups bewaren; de "
                                f"oudste worden verwijderd (default: "
                                f"{MAX_BACKUPS})")
    p_install.add_argument("--yes", action="store_true",
                           help="niet om bevestiging vragen")
    p_install.add_argument("--dry-run", action="store_true",
                           help="doorloop alle stappen zonder te schrijven")
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
