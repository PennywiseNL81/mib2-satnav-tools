#!/usr/bin/env python3
"""osutil.py -- OS-portability helpers for the MIB2 satnav tools.

Every place that touches something platform-specific lives here:
running external commands with progress, recursive copy / verify
(rsync when available, pure-Python fallback otherwise), enumerating
candidate SD mounts, and locating the 7-Zip binary.

Design rule: prefer *capability* detection (``shutil.which``, file
existence) over hard-coded OS checks; the OS branch is only the last
resort where there is no shared probe (e.g. /proc/mounts vs drive
letters).
"""

from __future__ import annotations

import os
import re
import shutil
import string
import subprocess

try:
    import pty as _pty
except ImportError:  # Windows
    _pty = None


class CommandError(Exception):
    """Raised when an external command exits non-zero."""


def rsync_pct(line: str):
    m = re.match(r"^\s*[\d.,]+\s+(\d+)%", line)
    return int(m.group(1)) if m else None


def sevenzip_pct(line: str):
    m = re.match(r"^\s*(\d+)%(?:\s|$)", line)
    return int(m.group(1)) if m else None


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


def run_cmd(cmd, cwd=None, pct_parser=None, progress_cb=None, log=None):
    """Run an external command, streaming its output.

    Uses a pty on POSIX (so progress-writing commands emit live percentage
    lines); on Windows, or when the pty setup fails, falls back to a plain
    pipe. ``pct_parser`` maps an output line to a percentage (0-100).
    Raises :class:`CommandError` on a non-zero exit code.
    """
    want_progress = pct_parser is not None and progress_cb is not None
    stream = None
    proc = None
    if want_progress and _pty is not None:
        master = slave = None
        try:
            master, slave = _pty.openpty()
            proc = subprocess.Popen(
                cmd, cwd=cwd, stdout=slave, stderr=slave,
                stdin=subprocess.DEVNULL, close_fds=True,
                env=dict(os.environ, LANG="C", LC_ALL="C"))
            os.close(slave)
            stream = os.fdopen(master, "rb", buffering=0)
        except OSError:
            for fd in (slave, master):
                if fd is not None:
                    try:
                        os.close(fd)
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
        raise CommandError(
            f"command failed (code {proc.returncode}): {' '.join(cmd)}")
    if want_progress and progress_cb:
        progress_cb(100)


def _have_rsync() -> bool:
    return shutil.which("rsync") is not None


def copy_tree(src: str, dst: str, progress_cb=None, log=None) -> None:
    """Recursively copy ``src`` into ``dst``, with optional progress (0-100).

    Uses ``rsync -a --info=progress2`` when available (POSIX), otherwise a
    pure-Python ``os.walk`` + ``shutil.copy2`` with file-count progress.
    """
    os.makedirs(dst, exist_ok=True)
    if _have_rsync():
        run_cmd(
            ["rsync", "-a", "--info=progress2",
             os.path.join(src, "") or src, os.path.join(dst, "") or dst],
            pct_parser=_rsync_pct, progress_cb=progress_cb, log=log)
        return

    files = [os.path.join(dp, f) for dp, _dn, fn in os.walk(src) for f in fn]
    total = len(files) or 1
    for i, p in enumerate(files, 1):
        rel = os.path.relpath(p, src)
        dest = os.path.join(dst, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copy2(p, dest)
        if log:
            log(f"copy: {rel}")
        if progress_cb:
            progress_cb(int(i * 100 / total))
    if progress_cb:
        progress_cb(100)


def _walk_entries(root: str):
    out = {}
    for dp, _dn, fn in os.walk(root):
        for f in fn:
            p = os.path.join(dp, f)
            rel = os.path.relpath(p, root)
            try:
                st = os.stat(p)
                out[rel] = (st.st_size, int(st.st_mtime))
            except OSError:
                out[rel] = None
    return out


def verify_tree(src: str, dst: str, exclude_rel=None) -> list:
    """Compare ``src`` against ``dst``; return the differing relative paths.

    Uses ``rsync -rcn`` when available, otherwise a pure-Python size+mtime
    walk. ``exclude_rel`` is a relative path (e.g. the workaround
    OVERALL.NDS) to skip on both sides.
    """
    excludes = ["--exclude=" + exclude_rel.replace(os.sep, "/")]
    if _have_rsync():
        src_arg = os.path.join(src, "") or src
        dst_arg = os.path.join(dst, "") or dst
        proc = subprocess.run(
            ["rsync", "-rcn", *excludes, "--out-format=%n", src_arg, dst_arg],
            capture_output=True, text=True,
            env=dict(os.environ, LANG="C", LC_ALL="C"))
        return [l for l in proc.stdout.splitlines() if l.strip()]

    def drop(excl):
        return {} if excl is None else {excl: None}

    a = dict(drop(exclude_rel), **(_walk_entries(src) or {}))
    b = dict(drop(exclude_rel), **(_walk_entries(dst) or {}))
    return sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))


def sd_mounts() -> list:
    """Candidate (device, mount) pairs for an inserted SD card.

    POSIX: parsed from /proc/mounts. Windows: every existing drive letter
    root (``A:``..``Z:``). The caller still has to check the content
    (maps/00/nds/dbinfo.txt) to decide whether it is the MIB2 card.
    """
    if os.name == "nt":
        out = []
        for letter in string.ascii_uppercase:
            root = f"{letter}:\\"
            try:
                if os.path.isdir(root):
                    out.append((f"{letter}:", root))
            except OSError:
                pass
        return out
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


def find_7z() -> str:
    """Locate a usable 7-Zip binary (``7z``/``7zz``/``7za``).

    PATH is searched first; on Windows the standard install directories
    (``%PROGRAMFILES%\\7-Zip\\7z.exe``, ``(x86)`` variant and
    ``%LOCALAPPDATA%\\Programs\\7-Zip\\7z.exe``) are probed as well.
    Returns None when not found.
    """
    for cand in ("7z", "7zz", "7za"):
        p = shutil.which(cand)
        if p:
            return p
    if os.name == "nt":
        candidates = []
        for envname, sub in (
                ("PROGRAMFILES", "7-Zip"),
                ("PROGRAMFILES(X86)", "7-Zip"),
                ("LOCALAPPDATA", os.path.join("Programs", "7-Zip"))):
            base = os.environ.get(envname)
            if base:
                candidates.append(os.path.join(base, sub, "7z.exe"))
        for p in candidates:
            if os.path.isfile(p):
                return p
    return None
