"""Generate bus route waypoints by MAP-MATCHING the official GTFS to OpenStreetMap
(pfaedle), instead of mirroring the CSDI geometry.

Why: the CSDI line is not GPS-observed — it is itself routed between stops (different
routes share byte-identical vertices over kilometres), so it inherits a router's
mistakes: spurious loops at termini and long detours where a turn was skipped
(e.g. route 15 never walks the correct road). Re-deriving the shape from the stop
sequence with a real map-matcher fixes every class at once. See PR for evidence.

Output is the SAME `waypoints/{ROUTE_ID}-{O|I}.json` GeoJSON format as `waypoints.py`
(ROUTE_ID == GTFS route_id; ROUTE_SEQ 1 -> "O", else "I"; coords truncated to 5 dp).

Requires Docker (pfaedle runs from the official image) — override with $PFAEDLE_CMD.
"""
import datetime
from zoneinfo import ZoneInfo
import csv
import json
import logging
import os
import re
import shutil
import subprocess
import urllib.request
import zipfile
from collections import OrderedDict
from tempfile import TemporaryDirectory

logging.basicConfig(level=logging.INFO)

GTFS_URL = "https://static.data.gov.hk/td/pt-headway-tc/gtfs.zip"
OSM_URL = "https://download.geofabrik.de/asia/china/hong-kong-latest.osm.pbf"
PFAEDLE_IMAGE = "ghcr.io/ad-freiburg/pfaedle:latest"
UNMATCHED_ABORT_RATE = 0.02   # abort the run if pfaedle leaves more than this fraction unmatched


def fetch(url: str, path: str):
    if os.path.exists(path):
        logging.info(f"using cached {path}")
        return
    logging.info(f"downloading {url}")
    with urllib.request.urlopen(url, timeout=300) as r, open(path, "wb") as f:
        shutil.copyfileobj(r, f)


def seq_of(route_id: str, trip_id: str) -> str:
    # trip_id is "<route_id>-<seq>-<service>-<departure>". Strip the route_id prefix so a
    # hyphen inside route_id can't be mistaken for the separator; seq 1 = outbound, 2 = inbound.
    if not trip_id.startswith(route_id + "-"):
        return ""
    return trip_id[len(route_id) + 1:].split("-")[0]


def build_min_gtfs(src_dir: str, out_dir: str) -> dict:
    """One representative trip per (route_id, direction); returns {trip_id: (route_id, seq)}."""
    os.makedirs(out_dir, exist_ok=True)

    def rd(name):
        with open(os.path.join(src_dir, name), encoding="utf-8-sig") as f:
            return list(csv.DictReader(f))

    routes = {r["route_id"]: r for r in rd("routes.txt")}
    kept = OrderedDict()          # (route_id, seq) -> trip_id ; seq is only ever "1"/"2"
    skipped = 0
    for t in rd("trips.txt"):
        rid = t["route_id"]
        if routes.get(rid, {}).get("route_type") != "3":   # franchised buses only
            continue
        seq = seq_of(rid, t["trip_id"])
        if seq not in ("1", "2"):                          # unexpected seq -> skip (never collide into O/I)
            skipped += 1
            continue
        kept.setdefault((rid, seq), t["trip_id"])
    if skipped:
        logging.warning(f"skipped {skipped} bus trips with unexpected trip_id seq (not 1/2)")
    keep_trips = set(kept.values())
    trip_meta = {tid: (rid, seq) for (rid, seq), tid in kept.items()}

    st = [s for s in rd("stop_times.txt") if s["trip_id"] in keep_trips]
    used_stops = {s["stop_id"] for s in st}
    used_routes = {rid for rid, _ in kept}

    def wr(name, header, rows):
        with open(os.path.join(out_dir, name), "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(header)
            w.writerows(rows)

    wr("agency.txt", ["agency_id", "agency_name", "agency_url", "agency_timezone"],
       [["HK", "HK", "https://hkbus.app", "Asia/Hong_Kong"]])
    wr("routes.txt", ["route_id", "agency_id", "route_short_name", "route_type"],
       [[rid, "HK", routes[rid].get("route_short_name", rid), "3"] for rid in used_routes])
    wr("trips.txt", ["route_id", "service_id", "trip_id"],
       [[rid, "D", tid] for tid, (rid, _) in trip_meta.items()])
    wr("stop_times.txt", ["trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence"],
       [[s["trip_id"], s["arrival_time"], s["departure_time"], s["stop_id"], s["stop_sequence"]] for s in st])
    stops = {s["stop_id"]: s for s in rd("stops.txt")}
    wr("stops.txt", ["stop_id", "stop_name", "stop_lat", "stop_lon"],
       [[sid, stops[sid].get("stop_name", ""), stops[sid]["stop_lat"], stops[sid]["stop_lon"]]
        for sid in used_stops if sid in stops])
    wr("calendar.txt", ["service_id", "monday", "tuesday", "wednesday", "thursday", "friday",
                        "saturday", "sunday", "start_date", "end_date"],
       [["D", 1, 1, 1, 1, 1, 1, 1, "20260101", "20261231"]])
    logging.info(f"minimal GTFS: {len(trip_meta)} route-directions, {len(used_stops)} stops")
    return trip_meta


def run_pfaedle(work: str):
    cmd = os.environ.get("PFAEDLE_CMD")
    if cmd:
        args = cmd.split() + ["-x", f"{work}/hong-kong.osm.pbf", "-o", f"{work}/out", f"{work}/gtfs"]
    else:
        args = ["docker", "run", "--rm", "-v", f"{work}:/data", PFAEDLE_IMAGE,
                "-x", "/data/hong-kong.osm.pbf", "-o", "/data/out", "/data/gtfs"]
    logging.info("running pfaedle: " + " ".join(args))
    subprocess.run(args, check=True)


def write_waypoints(work: str, trip_meta: dict):
    shapes = {}   # shape_id -> [[lon,lat],...]
    with open(f"{work}/out/shapes.txt", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            shapes.setdefault(row["shape_id"], []).append(
                (int(row["shape_pt_sequence"]), float(row["shape_pt_lon"]), float(row["shape_pt_lat"])))
    trip_shape = {}
    with open(f"{work}/out/trips.txt", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("shape_id"):
                trip_shape[row["trip_id"]] = row["shape_id"]

    written = 0
    unmatched = []
    seen = set()
    for tid, (rid, seq) in trip_meta.items():
        sid = trip_shape.get(tid)
        if not sid or sid not in shapes:
            unmatched.append(f"{rid}-{seq}")
            continue
        coords = [[lon, lat] for _, lon, lat in sorted(shapes[sid])]
        feature = {
            "type": "Feature",
            "properties": {"ROUTE_ID": rid, "ROUTE_SEQ": int(seq), "SOURCE": "pfaedle-osm"},
            "geometry": {"type": "LineString", "coordinates": coords},
        }
        name = f"waypoints/{rid}-{'O' if seq == '1' else 'I'}.json"
        if name in seen:                                   # must never happen (seq is 1/2 only) — fail loud
            raise RuntimeError(f"duplicate output filename {name}: direction/seq collision, aborting")
        seen.add(name)
        with open(name, "w", encoding="utf-8") as f:
            f.write(re.sub(
                r"([0-9]+\.[0-9]{5})[0-9]+", r"\1",
                json.dumps({"features": [feature], "type": "FeatureCollection"},
                           ensure_ascii=False, separators=(",", ":"))))
        written += 1
    logging.info(f"wrote {written} waypoint files; {len(unmatched)} route-directions unmatched by pfaedle")
    if unmatched:
        logging.warning("unmatched: " + ", ".join(unmatched[:60]) + (" ..." if len(unmatched) > 60 else ""))
    total = written + len(unmatched)
    rate = len(unmatched) / total if total else 0
    if rate > UNMATCHED_ABORT_RATE:
        raise RuntimeError(
            f"pfaedle left {rate:.1%} of route-directions unmatched (> {UNMATCHED_ABORT_RATE:.0%}); "
            "refusing to publish a partial dataset")
    return written


def main():
    # start from a clean output dir so a route dropped this run can't leave a stale file behind
    # (waypoints/ is gitignored; CI checks out clean, this covers local/repeated runs)
    if os.path.isdir("waypoints"):
        shutil.rmtree("waypoints")
    os.makedirs("waypoints", exist_ok=True)

    with TemporaryDirectory() as work:
        fetch(OSM_URL, f"{work}/hong-kong.osm.pbf")
        fetch(GTFS_URL, f"{work}/gtfs.zip")
        with zipfile.ZipFile(f"{work}/gtfs.zip") as z:
            z.extractall(f"{work}/gtfs_src")
        version = datetime.datetime.now(ZoneInfo("Asia/Hong_Kong")).isoformat()
        logging.info(f"gtfs fetched {version}")
        trip_meta = build_min_gtfs(f"{work}/gtfs_src", f"{work}/gtfs")
        run_pfaedle(work)
        write_waypoints(work, trip_meta)

    with open("waypoints/0versions.json", "w", encoding="utf-8") as f:
        json.dump({"bus": version, "source": "pfaedle-osm", "osm": OSM_URL}, f, indent=4)

    # keep the static rail/tram/ferry data, exactly like waypoints.py
    import glob
    for sub in ("mtr", "lrt", "ferry"):
        for file in glob.glob(f"./{sub}/*.json"):
            shutil.copy(file, "waypoints")


if __name__ == "__main__":
    main()
