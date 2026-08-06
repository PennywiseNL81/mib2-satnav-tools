#!/usr/bin/env python3
"""install.py -- one-command setup for mib2-satnav-tools.

- ensures the tool source is present (inside a git checkout: nothing; run
  standalone: pulls the repo archive from GitHub),
- creates a venv at mib2nds-tool/.venv and installs requirements.txt,
- checks for the 7-Zip binary (only needed for .7z packages) and prints
  install instructions when missing,
- optionally downloads the Natural Earth countries GeoJSON (--ne) used for
  the coverage-map borders.

Usage:
    python install.py                 # inside an existing checkout
    python install.py --ne            # also fetch Natural Earth borders
    python install.py [folder]        # standalone: pull the repo into <folder>
                                      # (default: current directory)
    MIB2_REPO_URL=<github-url> python install.py [folder]  # pull from a fork

A standalone install has no .git/, so re-running this in the same folder
updates the source in place and keeps your config.local.json, downloads/
and BACKUP/. Inside a real git checkout, update with git pull instead.

The Python running this must be 3.10+ (pure stdlib, no dependencies).
"""
from __future__ import annotations

import argparse
import io
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile

DEFAULT_REPO = "https://github.com/pennywiseNL81/mib2-satnav-tools"
REPO_URL = os.environ.get("MIB2_REPO_URL") or DEFAULT_REPO
ROOT = os.path.dirname(os.path.abspath(__file__))
NE_URL = ("https://raw.githubusercontent.com/nvkelso/natural-earth-vector/"
          "master/geojson/ne_50m_admin_0_countries.geojson")


def log(msg: str) -> None:
    print(msg)


def check_python() -> None:
    if sys.version_info < (3, 10):
        sys.exit(f"Python 3.10+ is required (found "
                 f"{sys.version_info.major}.{sys.version_info.minor}).")


def _has_venv_python(venv_dir: str) -> bool:
    for rel in ("bin/python", "Scripts/python.exe"):
        if os.path.isfile(os.path.join(venv_dir, rel)):
            return True
    return False


def _venv_python(venv_dir: str) -> str:
    rel = "Scripts/python.exe" if os.name == "nt" else "bin/python"
    return os.path.join(venv_dir, rel)


def is_checkout(root: str) -> bool:
    """True for a real git checkout (.git present). A standalone install has
    no .git, so re-running install.py in it pulls the latest snapshot over
    the existing files (an in-place update) instead of being mistaken for a
    checkout."""
    return os.path.isdir(os.path.join(root, ".git"))


def _zipball_url(url: str) -> str:
    """Accept a plain repo URL and turn it into a downloadable archive.

    ``https://github.com/user/repo`` serves HTML; the zipball is at
    ``.../archive/HEAD.zip`` (redirects to the default branch). Direct
    ``.zip`` URLs pass through unchanged.
    """
    if url.lower().endswith(".zip"):
        return url
    return url.rstrip("/") + "/archive/HEAD.zip"


def pull_repo(url: str, dest_dir: str) -> None:
    """Download the repo archive and extract it into dest_dir."""
    if not url:
        sys.exit("internal error: no repository URL to download")
    url = _zipball_url(url)
    log(f"downloading repo from {url}")
    with urllib.request.urlopen(url, timeout=60) as resp:
        data = resp.read()
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        top = zf.namelist()[0].split("/", 1)[0]
        for member in zf.namelist():
            if member.endswith("/"):
                continue
            rel = member[len(top) + 1:]
            if not rel:
                continue
            out = os.path.join(dest_dir, rel)
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with zf.open(member) as src, open(out, "wb") as dst:
                shutil.copyfileobj(src, dst)
    log(f"extracted into {dest_dir}")


def create_venv(tool_dir: str) -> str:
    venv_dir = os.path.join(tool_dir, ".venv")
    if _has_venv_python(venv_dir):
        log(f"venv already exists: {venv_dir}")
        return venv_dir
    log(f"creating venv: {venv_dir}")
    try:
        subprocess.check_call([sys.executable, "-m", "venv", venv_dir])
    except subprocess.CalledProcessError:
        if os.name != "nt":
            sys.exit(
                "Could not create the virtualenv (pip is missing in it).\n"
                "On Debian/Ubuntu install the python3-venv package first:\n"
                "    sudo apt install python3-venv\n"
                "then re-run this installer.")
        raise
    return venv_dir


def pip(venv_dir: str, *args: str) -> None:
    subprocess.check_call([_venv_python(venv_dir), "-m", "pip", *args])


def find_7z() -> str:
    for cand in ("7z", "7zz", "7za"):
        p = shutil.which(cand)
        if p:
            return p
    if os.name == "nt":
        for envname, sub in (
                ("PROGRAMFILES", "7-Zip"),
                ("PROGRAMFILES(X86)", "7-Zip"),
                ("LOCALAPPDATA", os.path.join("Programs", "7-Zip"))):
            base = os.environ.get(envname)
            if base:
                p = os.path.join(base, sub, "7z.exe")
                if os.path.isfile(p):
                    return p
    return None


def sevenzip_hint() -> None:
    p = find_7z()
    if p:
        log(f"7-Zip found: {p}")
        return
    log("7-Zip not found. It is only needed for .7z packages "
        "(folders and .zip work without it).")
    if os.name == "nt":
        log("Install it with:  winget install 7zip.7zip")
        log("  (or download it from https://www.7-zip.org)")
    else:
        log("Install it with:  sudo apt install p7zip-full   "
            "(Debian/Ubuntu)")


def download_ne(out_path: str) -> None:
    log(f"downloading Natural Earth countries to {out_path}")
    with urllib.request.urlopen(NE_URL, timeout=120) as resp:
        data = resp.read()
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "wb") as fh:
        fh.write(data)
    log(f"wrote {out_path} ({len(data) / 1e6:.1f} MB)")
    log("Point the tool at it with  NE_COUNTRIES=<path>  or "
        "dirs.ne_geojson in config.json.")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("dest", nargs="?", default=None,
                    help="standalone: target folder to install the repo into "
                         "(default: current directory)")
    ap.add_argument("--ne", action="store_true",
                    help="download the Natural Earth countries GeoJSON")
    ap.add_argument("--ne-out", default=None,
                    help="where to save the GeoJSON "
                         "(default: <repo>/ne_50m.geojson)")
    args = ap.parse_args(argv)
    check_python()

    if args.dest:
        dest = os.path.abspath(args.dest)
        if is_checkout(dest):
            root = dest
        else:
            os.makedirs(dest, exist_ok=True)
            pull_repo(REPO_URL, dest)
            root = dest
    else:
        root = ROOT
        if not is_checkout(root):
            if is_checkout(os.getcwd()):
                root = os.getcwd()
            else:
                pull_repo(REPO_URL, os.getcwd())
                root = os.getcwd()
    tool_dir = os.path.join(root, "mib2nds-tool")

    venv = create_venv(tool_dir)
    log("installing Python dependencies...")
    pip(venv, "install", "--upgrade", "pip")
    pip(venv, "install", "-r", os.path.join(root, "requirements.txt"))
    log("dependencies installed.")

    sevenzip_hint()
    if args.ne:
        download_ne(args.ne_out or os.path.join(root, "ne_50m.geojson"))

    log("First run: set up your car profile before checking maps - insert "
        "the SD card and run")
    log("  mib2nds-tool/.venv/bin/python sd-updater/update_sd.py profile")
    log("  (or use the web UI, step 1 'Car profile'). See README.md.")

    log("")
    log("Done. Start the web UI with:")
    log("  ./start-mapui.sh        (or start-mapui.bat on Windows)")
    log("or use the CLI: mib2nds-tool/.venv/bin/python "
        "mib2nds-tool/query.py --map <package> compat")
    return 0


if __name__ == "__main__":
    sys.exit(main())
