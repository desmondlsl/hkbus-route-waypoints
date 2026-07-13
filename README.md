# HK Bus WayPoints Crawling

![Data fetching status](https://github.com/hkbus/route-waypoints/actions/workflows/crawl.yml/badge.svg) 

This project is to fetch the waypoints from [CSDI](https://portal.csdi.gov.hk/geoportal/#metadataInfoPanel). It is daily synced to the sources and launched in gh-pages.

During the crawling, it will minified the result by truncating to 5 decimal places, (i.e., ±1m), and minified the json by cleaning useless space characters. Also, as the data is provided statically by `gh-pages`, the data transfer supports `Content-Encoding: gzip` for largely preserving your bandwidth.

Links are in format:
- MTR: https://hkbus.github.io/route-waypoints/{LINE_CODE}.json
- LRT: https://hkbus.github.io/route-waypoints/{LINE_NUMBER}-{[O|I]}.json
- Ferry: https://hkbus.github.io/route-waypoints/{ROUTE_ID}.json
- Everything else: https://hkbus.github.io/route-waypoints/{GTFS_ID}-{[O|I]}.json

Example link: (https://hkbus.github.io/route-waypoints/1001-O.json)

## Crawling by yourself

### Usage
Daily fetched GeoJSONs are in [gh-pages](https://github.com/hkbus/route-waypoints/tree/gh-pages).

### Installation

To install the dependencies,
```
pip install -r ./crawling/requirements.txt
```

### Data Fetching

To fetch data, run the followings,
```
python ./waypoints.py
```

## Experimental: waypoints from OpenStreetMap map-matching (`waypoints_pfaedle.py`)

The CSDI line is **not GPS-observed** — it is itself routed between stops, so it inherits a
router's mistakes: spurious multi-km loops at some termini and long detours where a turn was
skipped. `waypoints_pfaedle.py` instead **re-derives** each shape from the official GTFS stop
sequence by map-matching to OpenStreetMap with [`pfaedle`](https://github.com/ad-freiburg/pfaedle),
and writes the **same** `{ROUTE_ID}-{O|I}.json` format. It fixes every defect class at once and,
on a spot-check, lands within ~1% of the operators' own published route lines.

```
python waypoints_pfaedle.py          # needs Docker (pfaedle runs from the official image)
```

Runs the whole franchised-bus network in about a minute (OSM graph load + map-match). This is an
RFC — the manual `Data Fetching (pfaedle …)` workflow uploads the output as an artifact and does
**not** deploy to `gh-pages`. See the pull request for the evidence.

## Citing 

Please kindly state you are using this app as
`
HK Bus Crawling@2021, https://github.com/hkbus/route-waypoints
`

## Contributors
[ChunLaw](https://github.com/chunlaw/)
[chakflying](https://github.com/chakflying) 
