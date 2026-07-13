# HK Bus WayPoints Crawling

![Data fetching status](https://github.com/hkbus/route-waypoints/actions/workflows/crawl.yml/badge.svg) 

This project generates route waypoints by **map-matching the official GTFS stop sequence to OpenStreetMap** with [`pfaedle`](https://github.com/ad-freiburg/pfaedle). It is daily synced to the sources and launched in gh-pages.

> **Why not CSDI?** The [CSDI](https://portal.csdi.gov.hk/geoportal/#metadataInfoPanel) line is not GPS-observed — it is itself routed between stops (unrelated routes share kilometres of byte-identical vertices), so it inherits a router's mistakes: spurious multi-km loops at some termini and long detours where a turn was skipped (e.g. route 15 never walks the correct road). Re-deriving each shape from the stops with a real map-matcher fixes every class at once and lands within ~1% of the operators' own published lines. Covers buses **and** green minibuses (GTFS `route_type` 3) in one pass; rail/tram/ferry keep their curated static files.

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

### Data Fetching

`waypoints.py` needs only the standard library plus **Docker** (pfaedle runs from its official
image; Docker is preinstalled on GitHub's ubuntu runners). To fetch data:
```
python ./waypoints.py
```
It runs the whole road-transport network in about a minute (OSM graph load + map-match).

## Citing 

Please kindly state you are using this app as
`
HK Bus Crawling@2021, https://github.com/hkbus/route-waypoints
`

## Contributors
[ChunLaw](https://github.com/chunlaw/)
[chakflying](https://github.com/chakflying) 
