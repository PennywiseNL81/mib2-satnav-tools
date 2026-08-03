#!/usr/bin/env python3
"""nds2sqlite.py -- convert MIB2 (VW Group) NDS map databases to plain SQLite.

Input files use the 'ZV-zlib' ZipVFS container variant:

  * file header : dataStart @108, dataEnd @116, dbSize @140,
                  pageSize @172 (=65536), version @176
  * page map    : 8-byte big-endian entries starting at offset 200
                    offset = entry >> 24             (40 bits)
                    size   = (entry >> 7) & 0x1FFFF   (17 bits)
                    unused = entry & 0x7F
  * slot header : 6 bytes at page offset
                    pageno  = (u32[0:4] >> 1)         (31 bits)
                    pagelen = u32[2:6] & 0x1FFFF      (17 bits)
  * payload     : zlib stream at offset+6, 'size' bytes.
                  The first 4x16 bytes may be AES-128-ECB encrypted.
  * inflated    : one pageSize (=64 KiB) SQLite page.

The encryption key is auto-detected by trying the known MIB2 Standard key
list on page 0 and checking for the SQLite magic.  See pcbbc/NDS2SQLite and
lprot/MIB-Tools for the original references.
"""
from __future__ import annotations

import argparse
import os
import struct
import sys
import zlib
from concurrent.futures import ProcessPoolExecutor

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

MAGIC = b"ZV-zli"
SQLITE_MAGIC = b"SQLite"

KEYS = [
    b"My16BytePassword",
    b"z463rTyK9YS3JIPq",
    b"owTOajO2tcftkGWg",
    b"3TzgjvOpJYS1VNfa",
    b"8vJRhpfuytHTxWH2",
    b"5b2j5bLzM1lIdkiI",
    b"Lr2YMWxM3RRkB9GI",
    b"ELytUVOx2e6CIBCb",
    b"vLCgUQpEKnS8wx1J",
    b"HxFOBYqrya0QaDQN",
    b"5qiXreuKrL8g2iJK",
]


class NdsError(Exception):
    pass


def decrypt_payload(payload: bytes, key: bytes | None) -> bytes:
    if key is None:
        return payload
    blks = min(4, len(payload) // 16)
    dec = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
    return dec.update(payload[: blks * 16]) + payload[blks * 16:]


def inflate_payload(payload: bytes, key: bytes | None) -> bytes:
    return zlib.decompress(decrypt_payload(payload, key))


def iter_pages(data: bytes):
    """Yield (page_index, page_number, offset, payload)."""
    if data[:6] != MAGIC:
        raise NdsError("not a ZV-zlib file")
    data_start = struct.unpack_from(">Q", data, 108)[0]
    db_size = struct.unpack_from(">Q", data, 140)[0]
    page_size = struct.unpack_from(">I", data, 172)[0]
    n_pages = db_size // page_size
    for p in range(n_pages):
        entry = struct.unpack_from(">Q", data, 200 + p * 8)[0]
        offset = entry >> 24
        size = (entry >> 7) & 0x1FFFF
        if offset == 0:
            yield p, 0, 0, b""
            continue
        slot = data[offset : offset + 6]
        pageno = struct.unpack(">I", slot[:4])[0] >> 1
        payload = data[offset + 6 : offset + 6 + size]
        yield p, pageno, offset, payload


def detect_key(data: bytes) -> bytes | None:
    """Return the AES key (or None for unencrypted) that makes page 0
    inflate to a valid SQLite page."""
    for p, pageno, offset, payload in iter_pages(data):
        if p != 0:
            break
        for key in [None] + KEYS:
            try:
                out = inflate_payload(payload, key)
            except zlib.error:
                continue
            if out[:6] == SQLITE_MAGIC:
                return key
    raise NdsError("no known key produced a valid SQLite page 0")


def convert_bytes(data: bytes, key: bytes | None, on_page=None) -> bytearray:
    """Inflate every page and return the SQLite database bytes."""
    if data[:6] != MAGIC:
        raise NdsError("not a ZV-zlib file")
    db_size = struct.unpack_from(">Q", data, 140)[0]
    page_size = struct.unpack_from(">I", data, 172)[0]
    out = bytearray(db_size)
    n = 0
    for p, pageno, offset, payload in iter_pages(data):
        if pageno == 0:
            continue  # empty / free page -> zero fill
        raw = inflate_payload(payload, key)
        if len(raw) > page_size:
            raise NdsError(f"page {p} inflated to {len(raw)} > {page_size}")
        start = (pageno - 1) * page_size
        out[start : start + len(raw)] = raw
        n += 1
        if on_page:
            on_page(p, n)
    return out


def convert_file(in_path: str, out_path: str, key: bytes | None = None,
                 force: bool = False, quiet: bool = False) -> tuple[bytes | None, int]:
    if not force and os.path.exists(out_path):
        if not quiet:
            print(f"skip (exists): {out_path}")
        return None, 0
    with open(in_path, "rb") as fh:
        data = fh.read()
    if key is None:
        key = detect_key(data)
    db_size = struct.unpack_from(">Q", data, 140)[0]
    out = convert_bytes(data, key)
    if len(out) != db_size:
        raise NdsError(
            f"{in_path}: converted {len(out)} != header dbSize {db_size}")
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "wb") as fh:
        fh.write(out)
    if not quiet:
        print(f"{in_path}: key={key.decode(errors='replace') if key else 'none'} "
              f"-> {out_path} ({len(out)} bytes)")
    return key, len(out)


def convert_tree(root: str, out_root: str, force: bool = False,
                 workers: int = 1, quiet: bool = False) -> None:
    jobs = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in filenames:
            if not fn.lower().endswith(".nds"):
                continue
            in_path = os.path.join(dirpath, fn)
            rel = os.path.relpath(in_path, root)
            out_path = os.path.join(out_root, rel[:-4] + ".sqlite")
            jobs.append((in_path, out_path))
    if not jobs:
        print(f"no .nds files under {root}")
        return
    if workers == 1 or len(jobs) == 1:
        keys = {}
        for in_path, out_path in jobs:
            key, _ = convert_file(in_path, out_path, force=force, quiet=quiet)
            if key:
                keys[in_path] = key.decode(errors="replace")
        return
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(convert_file, in_path, out_path, None, force, quiet)
            for in_path, out_path in jobs
        }
        for fut in futures:
            fut.result()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("convert", help="convert a single .nds file")
    p1.add_argument("input")
    p1.add_argument("-o", "--output", required=True)
    p1.add_argument("--key", default=None, help="AES key (auto-detect if omitted)")
    p1.add_argument("--force", action="store_true")

    p2 = sub.add_parser("tree", help="convert every .nds under a directory")
    p2.add_argument("input")
    p2.add_argument("output")
    p2.add_argument("-j", "--jobs", type=int, default=1)
    p2.add_argument("--force", action="store_true")
    p2.add_argument("--quiet", action="store_true")

    p3 = sub.add_parser("keys", help="list known MIB2 Standard map keys")
    p3.add_argument("--full", action="store_true")

    args = ap.parse_args(argv)
    if args.cmd == "keys":
        for k in KEYS:
            print(k.decode())
        if args.full:
            print("AES-128-ECB, IV not used, first 4x16 bytes of each page payload")
        return 0
    if args.cmd == "convert":
        convert_file(args.input, args.output, key=args.key, force=args.force)
        return 0
    if args.cmd == "tree":
        convert_tree(args.input, args.output, force=args.force,
                     workers=args.jobs, quiet=args.quiet)
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
