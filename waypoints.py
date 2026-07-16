import datetime
from zoneinfo import ZoneInfo
import requests
import json
import re
import os
import zipfile
import io
import glob
import shutil
import geopandas
from tempfile import TemporaryDirectory
import logging

logging.basicConfig(level=logging.INFO)


def store_version(key: str, version: str):
    logging.info(f"{key} version: {version}")
    # "0" is prepended in filename so that this file appears first in Github directory listing
    try:
        with open('waypoints/0versions.json', 'r') as f:
            version_dict = json.load(f)
    except BaseException:
        version_dict = {}
    version_dict[key] = version
    version_dict = dict(sorted(version_dict.items()))
    with open('waypoints/0versions.json', 'w', encoding='UTF-8') as f:
        json.dump(version_dict, f, indent=4)


# --- O/I direction resolution -------------------------------------------
# CSDI writes ROUTE_SEQ (1/2), which does NOT track the operators' outbound (O) /
# inbound (I) convention, so labelling by ROUTE_SEQ swaps some routes (issue #14).
# We resolve direction from hkbus's own routeFareList (the operators' O/I with
# terminus names, per direction) and match it to each CSDI feature's start/end.

# CSDI COMPANY_CODE -> hkbus routeFareList "co"
_CO_ALIAS = {
    "LWB": "kmb",
    "KMB": "kmb",
    "CTB": "ctb",
    "NLB": "nlb",
    "GMB": "gmb"}


def normalize_stop_name(name):
    """Lowercase, strip HTML/terminus suffixes/punctuation for fuzzy comparison.

    CSDI and operator datasets name the same terminus differently
    (e.g. "TUNG CHUNG DEVELOPMENT PIER" vs "Tung Chung Pier"), so an exact
    match is not possible for a large share of routes.
    """
    if not name:
        return ""
    name = re.sub(r"<[^>]+>", "", name).lower().strip()
    for suffix in ["bus terminus", "bus termini", "bus station", "station",
                   "terminus", "termini", "estate", "pier", "development"]:
        name = re.sub(rf"\b{re.escape(suffix)}\b", "", name)
    name = re.sub(r"[/,()\-\\&'<>]", " ", name)
    return re.sub(r"\s+", " ", name).strip()


def name_matches(a, b):
    na, nb = normalize_stop_name(a), normalize_stop_name(b)
    if not na or not nb:
        return False
    if na in nb or nb in na:
        return True
    wa = {w for w in na.split() if len(w) >= 3}
    wb = {w for w in nb.split() if len(w) >= 3}
    return bool(wa and wb) and len(wa & wb) >= min(2, len(wa), len(wb))


def fetch_route_directions():
    """(co, route) -> {"O": (orig, dest), "I": (orig, dest)} from routeFareList."""
    directions = {}
    try:
        route_list = requests.get(
            "https://data.hkbus.app/routeFareList.json", timeout=60
        ).json()["routeList"]
        for v in route_list.values():
            for co in v.get("co", []):
                bound = v.get("bound", {}).get(co)
                if bound:
                    directions.setdefault((co, v["route"]), {})[bound] = (
                        v["orig"]["en"], v["dest"]["en"])
        logging.info(
            f"Fetched directions for {
                len(directions)} (co, route) pairs")
    except Exception as e:
        logging.warning(
            f"routeFareList fetch failed; using ROUTE_SEQ fallback: {e}")
    return directions


def _score(props, orig, dest):
    return ((1 if orig and name_matches(props.get("ST_STOP_NAMEE", ""), orig) else 0) +
            (1 if dest and name_matches(props.get("ED_STOP_NAMEE", ""), dest) else 0))


def assign_directions(features, route_directions):
    """Assign O/I to every feature of one route, together.

    Resolving a route's features jointly guarantees a two-direction route gets
    exactly one O and one I (no file overwrites another), placed in whichever
    orientation best matches the operators' origin/destination. Falls back to
    ROUTE_SEQ when routeFareList has no usable entry or can't disambiguate.
    """
    def seq_label(f):
        return "O" if f["properties"].get("ROUTE_SEQ", 1) == 1 else "I"

    if not features:
        return []
    p0 = features[0]["properties"]
    co = _CO_ALIAS.get(
        p0.get(
            "COMPANY_CODE", ""), p0.get(
            "COMPANY_CODE", "").lower())
    d = route_directions.get((co, p0.get("ROUTE_NAMEE", "")))
    if not d:
        return [seq_label(f) for f in features]
    o_orig, o_dest = d.get("O", (None, None))
    i_orig, i_dest = d.get("I", (None, None))

    if len(features) == 1:
        so = _score(features[0]["properties"], o_orig, o_dest)
        si = _score(features[0]["properties"], i_orig, i_dest)
        return [
            seq_label(
                features[0])] if so == si else (
            ["O"] if so > si else ["I"])

    if len(features) == 2:
        a, b = (f["properties"] for f in features)
        keep = _score(a, o_orig, o_dest) + _score(b, i_orig, i_dest)
        swap = _score(a, i_orig, i_dest) + _score(b, o_orig, o_dest)
        if keep != swap:
            return ["O", "I"] if keep > swap else ["I", "O"]
        first = seq_label(features[0])
        return [first, "I" if first == "O" else "O"]

    return [seq_label(f) for f in features]


os.makedirs("waypoints", exist_ok=True)
route_directions = fetch_route_directions()

for csdi_dataset in [
    # 巴士路線
    # https://portal.csdi.gov.hk/geoportal/?lang=zh-hk&datasetId=td_rcd_1638844988873_41214
    {"name": "bus", "id": "td_rcd_1638844988873_41214"},
    # 專線小巴路線
    # https://portal.csdi.gov.hk/geoportal/?lang=zh-hk&datasetId=td_rcd_1697082463580_57453
    {"name": "gmb", "id": "td_rcd_1697082463580_57453"}
]:
    logging.info("csdi_dataset=" + json.dumps(csdi_dataset))
    logging.info("Fetching metadata")
    r = requests.get(
        "https://portal.csdi.gov.hk/geoportal/rest/metadata/item/" +
        csdi_dataset["id"])
    src_id = json.loads(r.content)['_source']['fileid'].replace('-', '')

    logging.info("Fetching FGDB")
    r = requests.get(
        "https://static.csdi.gov.hk/csdi-webpage/download/" + src_id + "/fgdb")
    z = zipfile.ZipFile(io.BytesIO(r.content))
    version = min([f.date_time for f in z.infolist()])
    version = datetime.datetime(
        *version, tzinfo=ZoneInfo("Asia/Hong_Kong"))
    store_version(csdi_dataset["name"], version.isoformat())
    gdb_name = next(s[0:s.index('/')]
                    for s in z.namelist() if s != "__MACOSX")

    with TemporaryDirectory() as tmpdir:
        logging.info("Extracting data")
        z.extractall(tmpdir)
        gdb_path = os.path.join(tmpdir, gdb_name)
        logging.info("Reading data (1)")
        gdf = geopandas.read_file(gdb_path, encoding='utf-8')
        logging.info("Transforming data")
        gdf.to_crs(epsg=4326, inplace=True)
        logging.info("Reading data (2)")
        data = gdf.to_geo_dict(drop_id=True)

    logging.info("Storing data")
    features_by_route = {}
    for feature in data["features"]:
        features_by_route.setdefault(
            feature["properties"]["ROUTE_ID"], []).append(feature)

    for route_id, route_features in features_by_route.items():
        directions = assign_directions(route_features, route_directions)
        for feature, direction in zip(route_features, directions):
            with open("waypoints/" + str(route_id) + "-" + direction + ".json", "w", encoding='utf-8') as f:
                f.write(
                    re.sub(
                        r"([0-9]+\.[0-9]{5})[0-9]+",
                        r"\1",
                        json.dumps({
                            "features": [feature],
                            "type": "FeatureCollection"
                        },
                            ensure_ascii=False,
                            separators=(",", ":")
                        )
                    )
                )


logging.info("Copying static data")
for file in glob.glob(r'./mtr/*.json'):
    shutil.copy(file, "waypoints")
for file in glob.glob(r'./lrt/*.json'):
    shutil.copy(file, "waypoints")
for file in glob.glob(r'./ferry/*.json'):
    shutil.copy(file, "waypoints")
