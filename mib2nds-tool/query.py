#!/usr/bin/env python3
"""Query MIB2 NDS map data (converted to SQLite) for place names and coverage.

Subcommands:
  search <name>       Find places by (exact) name in the full-text name index.
  countries           List update regions and the countries they cover.
  coverage [--out X] [--osm]  Render a Europe map with the actual data coverage.
  compat [--wanted ..] Check whether a map package fits the reference car.
"""

import argparse
import json
import math
import os
import sqlite3
import sys

import matplotlib

matplotlib.use("Agg")

import mapdata
import ndsgeo


def _merc(lat):
    """Mercator y (in degrees) for a latitude, so the map is conformal."""
    return math.degrees(math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)))


def _fmt_row(row):
    lat, lon = ndsgeo.morton_to_ll(row["mortonCode"])
    return {
        "namedObjectId": row["namedObjectId"],
        "name": row["criterionB"],
        "postalCode": row["criterionC"],
        "lat": round(lat, 5),
        "lon": round(lon, 5),
        "updateRegion": row["updateRegionId"],
        "regionLabel": ndsgeo.region_label(row["updateRegionId"]),
    }


def _map_from_args(args):
    source = mapdata.resolve_source(args.map)
    val = mapdata.validate(source)
    if not val["ok"]:
        print("invalid map:", "; ".join(val["errors"]), file=sys.stderr)
        sys.exit(2)
    nds_out = mapdata.nds_out_dir(source)
    mapdata.ensure_countries(source, nds_out)
    if args.cmd in ("search", "coverage"):
        mapdata.ensure_search(source, nds_out)
    return mapdata.Map(source, nds_out,
                       ne_path=getattr(args, "ne", None) or mapdata.DEFAULT_NE)


def cmd_search(args):
    if getattr(args, "map", None):
        m = _map_from_args(args)
        name = " ".join(args.name)
        res = mapdata.search(m, name,
                             mode="contains" if args.contains else "exact")
        print(f"'{name}': {res['total']} unique object(s)")
        for it in res["results"]:
            zip_s = f"  zip={it['postalCode']}" if it["postalCode"] else ""
            print(f"  {it['name']}  ({it['lat']:.4f}, {it['lon']:.4f})"
                  f"  region={it['regionLabel']}{zip_s}")
        return 0
    conn = ndsgeo.open_product(args.db)
    name = " ".join(args.name)
    if args.contains:
        rows = conn.execute(
            "select namedObjectId, mortonCode, updateRegionId, criterionB, criterionC "
            "from nameFtsTable where criterionB like ?",
            ("%" + name + "%",),
        ).fetchall()
    else:
        rows = conn.execute(
            "select namedObjectId, mortonCode, updateRegionId, criterionB, criterionC "
            "from nameFtsTable where criterionB = ?",
            (name,),
        ).fetchall()
    if not rows:
        print(f"'{name}': no matches in the name index.")
        return 1
    seen = {}
    for r in rows:
        key = r["namedObjectId"]
        seen.setdefault(key, []).append(_fmt_row(r))
    print(f"'{name}': {len(rows)} index row(s), {len(seen)} unique named object(s)\n")
    for key, items in seen.items():
        first = items[0]
        extra = "" if len(items) == 1 else f" (+{len(items) - 1} language rows)"
        print(f"  {first['name']}  ({first['lat']:.4f}, {first['lon']:.4f})"
              f"  region={first['regionLabel']}"
              + (f"  zip={first['postalCode']}" if first["postalCode"] else "")
              + extra)
    return 0


def cmd_countries(args):
    if getattr(args, "map", None):
        source = mapdata.resolve_source(args.map)
        val = mapdata.validate(source)
        if not val["ok"]:
            print("invalid map:", "; ".join(val["errors"]), file=sys.stderr)
            return 2
        nds_out = mapdata.nds_out_dir(source)
        mapdata.ensure_countries(source, nds_out)
        regions = mapdata.read_countries(nds_out, val["regions"])
        info = val["info"]
        print(f"{source.name}: {info.get('SystemName', '?')} "
              f"v{info.get('ApplicationSoftwareVersionNumber', '?')}")
        for r in regions:
            names = ", ".join(f"{c} ({mapdata.country_name(c)})" for c in r["countries"])
            print(f"  {r['dir']}: {names or '(no country codes)'}")
        all_codes = sorted({c for r in regions for c in r["countries"]})
        print(f"\n  Total covered ({len(all_codes)}): " + ", ".join(all_codes))
        return 0
    for rid in sorted(ndsgeo.REGION_INFO):
        info = ndsgeo.REGION_INFO[rid]
        print(f"  updateRegion {rid}: {info['nds_dir']}  ->  {', '.join(info['countries'])}")
    print()
    print(f"  Covered countries ({len(ndsgeo.COVERED_ISO)}): "
          + ", ".join(ndsgeo.COVERED_ISO))
    return 0


def _load_ne(path):
    with open(path) as fh:
        data = json.load(fh)
    out = []
    for feat in data["features"]:
        code = feat["properties"].get("ADM0_A3")
        admin = feat["properties"].get("ADMIN", "")
        geom = feat["geometry"]
        if geom["type"] == "Polygon":
            polys = [geom["coordinates"]]
        elif geom["type"] == "MultiPolygon":
            polys = geom["coordinates"]
        else:
            continue
        for poly in polys:
            rings = [[(p[0], p[1]) for p in ring] for ring in poly]
            out.append({"code": code, "admin": admin, "rings": rings})
    return out


def cmd_coverage(args):
    if getattr(args, "map", None):
        m = _map_from_args(args)
        stats = mapdata.render_coverage(m, args.out, dpi=args.dpi,
                                        ne_path=args.ne or mapdata.DEFAULT_NE,
                                        background="osm" if args.osm else None)
        print(f"wrote {args.out}", file=sys.stderr)
        for rid, s in sorted(stats["regions"].items()):
            print(f"  region {rid} ({s['label']}): {s['count']} points",
                  file=sys.stderr)
        print(f"  bbox: {stats['bbox']}", file=sys.stderr)
        return 0
    import matplotlib.pyplot as plt
    import matplotlib.patheffects as pe

    conn = ndsgeo.open_product(args.db)
    rows = conn.execute(
        "select mortonCode, updateRegionId from nameFtsTable where mortonCode is not null"
    ).fetchall()
    print(f"decoding {len(rows)} name index entries...", file=sys.stderr)

    y_min, y_max = _merc(25), _merc(72)
    pts = {rid: ([], []) for rid in ndsgeo.REGION_INFO}
    for r in rows:
        rid = r["updateRegionId"]
        if rid not in pts:
            continue
        lat, lon = ndsgeo.morton_to_ll(r["mortonCode"])
        if -27 <= lon <= 12 and 25 <= lat <= 72:
            pts[rid][0].append(lon)
            pts[rid][1].append(_merc(lat))

    fig, ax = plt.subplots(figsize=(16, 13))
    colors = {0: "#d62728", 3: "#1f77b4", 6: "#2ca02c", 7: "#9467bd"}

    ne_path = args.ne or mapdata.DEFAULT_NE
    if os.path.exists(ne_path):
        for poly in _load_ne(ne_path):
            covered = poly["code"] in ndsgeo.COVERED_ISO
            for ring in poly["rings"]:
                xs = [p[0] for p in ring]
                ys = [_merc(p[1]) for p in ring]
                ax.plot(xs, ys, color="#666666", lw=0.4, zorder=1)
                if covered:
                    ax.fill(xs, ys, color="#eeee77", alpha=0.45, lw=0, zorder=1)
    else:
        print(f"warning: Natural Earth file not found at {ne_path}", file=sys.stderr)

    for rid in sorted(pts):
        xs, ys = pts[rid]
        if not xs:
            continue
        print(f"  region {rid} ({ndsgeo.region_label(rid)}): {len(xs)} points", file=sys.stderr)
        hb = ax.hexbin(
            xs, ys, gridsize=120, mincnt=1,
            cmap=plt.cm.colors.LinearSegmentedColormap.from_list(
                "r", [(1, 1, 1, 0), colors[rid]]),
            extent=(-27, 12, y_min, y_max), zorder=2, linewidths=0,
        )
        hb.set_alpha(0.6)

    ax.set_xlim(-27, 12)
    ax.set_ylim(y_min, y_max)
    ax.set_aspect("equal")
    from matplotlib.ticker import FuncFormatter
    lat_ticks = [30, 40, 50, 60, 70]
    ax.set_yticks([_merc(t) for t in lat_ticks],
                  [f"{t}N" for t in lat_ticks])
    lon_ticks = [-25, -15, -5, 5]
    ax.set_xticks(lon_ticks, [f"{t}E" if t >= 0 else f"{-t}W" for t in lon_ticks])
    ax.set_xlabel("longitude")
    ax.set_ylabel("latitude")
    ax.set_title("MIB2 map v2510 EU1 - name index coverage\n"
                 "STD2_2510_EU1_202525 (every dot = place name in the map data)")
    handles = [
        plt.Line2D([], [], marker="s", ls="", color=colors[rid], label=ndsgeo.region_label(rid))
        for rid in sorted(pts)
    ]
    ax.legend(handles=handles, loc="lower left", framealpha=0.9)

    out = args.out
    fig.savefig(out, dpi=args.dpi, bbox_inches="tight")
    print(f"wrote {out}", file=sys.stderr)
    return 0


def cmd_compat(args):
    if not args.map:
        print("compat requires --map <folder|zip>", file=sys.stderr)
        return 2
    source = mapdata.resolve_source(args.map)
    val = mapdata.validate(source)
    if not val["ok"]:
        print("invalid map:", "; ".join(val["errors"]), file=sys.stderr)
        return 2
    wanted = None
    if args.wanted:
        wanted = [w.strip().upper() for w in args.wanted.split(",") if w.strip()]
    compat = mapdata.compatibility_check(source, wanted)
    print(f"{source.name}: {compat['verdict']}")
    print(f"  car: {compat['car']['make']} · {compat['car']['nav_series']} "
          f"(part {compat['car']['part_number']})")
    for ch in compat["checks"]:
        mark = {True: "ok", False: "!!", None: "--"}[ch["ok"]]
        print(f"  [{mark}] {ch['label']}: {ch['detail']}")
    print(f"  size: {compat['size']['gb']:.1f} GB extracted")
    if compat["missing"]:
        print("  missing wanted countries: " + ", ".join(compat["missing"]))
    print("  installation:")
    for i, s in enumerate(compat["install"]["steps"], 1):
        print(f"    {i}. {s}")
    if (compat["install"]["workaround_enabled"]
            and not compat["install"]["overall_backup_present"]):
        print(f"  WARNING: OVERALL.NDS backup missing: "
              f"{compat['install']['overall_backup']}")
    return 0 if compat["verdict_ok"] else 3


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=ndsgeo.DEFAULT_PRODUCT_DB,
                    help="path to the converted PRODUCT/PRODUCT.sqlite")
    ap.add_argument("--map", default=None,
                    help="map package (folder with maps/ or .zip) to load "
                         "instead of --db; converts on demand")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("search", help="find places by name")
    p.add_argument("name", nargs="+", help="place name (may be multiple words)")
    p.add_argument("--contains", action="store_true",
                   help="substring match instead of exact")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("countries", help="list regions and covered countries")
    p.set_defaults(func=cmd_countries)

    p = sub.add_parser("coverage", help="render a Europe coverage map")
    p.add_argument("--out", default=os.path.join(mapdata.WORK, "coverage_eu1.png"))
    p.add_argument("--dpi", type=int, default=130)
    p.add_argument("--ne", help="Natural Earth countries GeoJSON path")
    p.add_argument("--osm", action="store_true",
                   help="draw an OpenStreetMap background (like the web UI) "
                        "instead of a transparent image")
    p.set_defaults(func=cmd_coverage)

    p = sub.add_parser("compat", help="is this map compatible with the car?")
    p.add_argument("--wanted", default=None,
                   help="comma-separated wanted ISO3 codes (default: car profile)")
    p.set_defaults(func=cmd_compat)

    args = ap.parse_args()
    notice = mapdata.first_run_notice()
    if notice:
        print(f"note: {notice}", file=sys.stderr)
    sys.exit(args.func(args) or 0)


if __name__ == "__main__":
    main()
