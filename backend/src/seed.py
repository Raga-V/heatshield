"""Seeds water / shade / fuel / toilet points for the city from OpenStreetMap (Overpass API).

Event: {} uses the BBOX env var. Optional {"extra": [{"lat":..,"lon":..,"kind":"water","name":".."}]}
adds hand-checked points (useful where OSM coverage is thin).
"""
import json
import os
import urllib.parse
import urllib.request
from decimal import Decimal

import boto3

import core

CFG = core.load_config()
BBOX = tuple(float(x) for x in os.environ.get("BBOX", "17.30,78.30,17.56,78.60").split(","))
POINTS = boto3.resource("dynamodb").Table(os.environ.get("POINTS_TABLE", "points"))
# Public Overpass servers are often overloaded; try each in turn.
OVERPASS = [u for u in os.environ.get("OVERPASS_URL", "").split(",") if u] or [
    "https://overpass-api.de/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

KINDS = {
    ("amenity", "drinking_water"): "water",
    ("amenity", "water_point"): "water",
    ("amenity", "fuel"): "fuel",
    ("amenity", "toilets"): "toilets",
    ("amenity", "shelter"): "shade",
}


def query(bbox):
    s, w, n, e = bbox
    b = f"{s},{w},{n},{e}"
    return (f'[out:json][timeout:90];('
            f'node["amenity"="drinking_water"]({b});node["amenity"="water_point"]({b});'
            f'node["amenity"="fuel"]({b});way["amenity"="fuel"]({b});'
            f'node["amenity"="toilets"]({b});node["amenity"="shelter"]({b});'
            f');out center tags;')


def fetch(bbox):
    data = urllib.parse.urlencode({"data": query(bbox)}).encode()
    errors = []
    for url in OVERPASS:
        req = urllib.request.Request(url, data=data, headers={"User-Agent": "heatshield-hackathon/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=50) as r:
                return json.loads(r.read())["elements"], None
        except Exception as e:  # try the next server
            errors.append(f"{url}: {e}")
    return [], "; ".join(errors)


def to_point(el):
    tags = el.get("tags", {})
    kind = KINDS.get(("amenity", tags.get("amenity")))
    lat = el.get("lat") or el.get("center", {}).get("lat")
    lon = el.get("lon") or el.get("center", {}).get("lon")
    if not kind or lat is None:
        return None
    return {"pointId": f"osm-{el['type']}-{el['id']}", "lat": lat, "lon": lon, "kind": kind,
            "name": tags.get("name") or tags.get("brand") or "", "source": "osm", "verified": True,
            "cellId": core.cell_id(lat, lon, CFG["cellDeg"])}


def handler(event, _context):
    event = event or {}
    elements, osm_error = ([], None) if event.get("skipOsm") else fetch(BBOX)
    points = [p for p in (to_point(e) for e in elements) if p]
    for i, x in enumerate(event.get("extra", [])):
        points.append({"pointId": f"manual-{i}", "lat": x["lat"], "lon": x["lon"], "kind": x["kind"],
                       "name": x.get("name", ""), "source": "team-verified", "verified": True,
                       "cellId": core.cell_id(x["lat"], x["lon"], CFG["cellDeg"])})
    with POINTS.batch_writer(overwrite_by_pkeys=["pointId"]) as bw:
        for p in points:
            bw.put_item(Item=json.loads(json.dumps(p), parse_float=Decimal))
    counts = {}
    for p in points:
        counts[p["kind"]] = counts.get(p["kind"], 0) + 1
    out = {"seeded": len(points), "byKind": counts}
    if osm_error:
        out["osmError"] = osm_error  # extra points were still written
    return out
