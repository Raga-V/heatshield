"""Builds the city's WBGT grid from Open-Meteo.

Event shapes:
  {"mode": "forecast"}                       hourly schedule: now .. now+6 h
  {"mode": "replay", "date": "2024-05-03"}   a past heatwave day, hours 08..19
  {"mode": "scan", "from": "2024-04-01", "to": "2024-06-15"}
                                             returns the hottest days at the city centre
"""
import json
import os
import time
import urllib.parse
import urllib.request
from decimal import Decimal

import boto3

import core

CFG = core.load_config()
BBOX = tuple(float(x) for x in os.environ.get("BBOX", "17.30,78.30,17.56,78.60").split(","))
BUCKET = os.environ.get("DATA_BUCKET", "")
TZ = os.environ.get("TZ_NAME", "Asia/Kolkata")
HOURLY = "temperature_2m,relative_humidity_2m,wind_speed_10m,shortwave_radiation"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

_s3 = boto3.client("s3")
GRID = boto3.resource("dynamodb").Table(os.environ.get("GRID_TABLE", "grid"))


def http_json(url, params, tries=3):
    full = url + "?" + urllib.parse.urlencode(params, safe=",")
    for attempt in range(tries):
        try:
            req = urllib.request.Request(full, headers={"User-Agent": "heatshield-hackathon/1.0"})
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.loads(r.read())
        except Exception:
            if attempt == tries - 1:
                raise
            time.sleep(2 * (attempt + 1))


def fetch(cells, url, extra):
    """Returns {cellId: hourly dict}, batching 50 coordinates per call."""
    out = {}
    for i in range(0, len(cells), 50):
        chunk = cells[i:i + 50]
        centres = [core.cell_center(c, CFG["cellDeg"]) for c in chunk]
        params = {"latitude": ",".join(f"{a:.4f}" for a, _ in centres),
                  "longitude": ",".join(f"{b:.4f}" for _, b in centres),
                  "hourly": HOURLY, "timezone": TZ, "wind_speed_unit": "ms"}
        params.update(extra)
        data = http_json(url, params)
        if isinstance(data, dict):
            data = [data]
        for cid, d in zip(chunk, data):
            out[cid] = d["hourly"]
    return out


def compute(hourly_by_cell, wanted):
    """wanted: {open-meteo time prefix 'YYYY-MM-DDTHH': our hourKey}. Returns {hourKey: [cells]}."""
    by_hour = {}
    for cid, h in hourly_by_cell.items():
        for idx, t in enumerate(h["time"]):
            key = wanted.get(t[:13])
            if not key:
                continue
            temp, rh = h["temperature_2m"][idx], h["relative_humidity_2m"][idx]
            if temp is None or rh is None:
                continue
            sw = h["shortwave_radiation"][idx] or 0
            w = core.wbgt_outdoor(temp, rh, sw, CFG)
            by_hour.setdefault(key, []).append({
                "cellId": cid, "hourKey": key, "wbgt": w, "band": core.band_for(w, CFG),
                "tempC": temp, "rh": rh, "swRad": sw, "windMs": h["wind_speed_10m"][idx]})
    return by_hour


def store(by_hour, ttl_days):
    expires = int(time.time()) + ttl_days * 86400
    with GRID.batch_writer(overwrite_by_pkeys=["cellId", "hourKey"]) as bw:
        for cells in by_hour.values():
            for c in cells:
                item = dict(c, expiresAt=expires)
                bw.put_item(Item=json.loads(json.dumps(item), parse_float=Decimal))
    summary = {}
    for key, cells in by_hour.items():
        features = [{"type": "Feature",
                     "geometry": {"type": "Polygon", "coordinates": [core.cell_polygon(c["cellId"], CFG["cellDeg"])]},
                     "properties": {"wbgt": c["wbgt"], "band": c["band"], "tempC": c["tempC"], "rh": c["rh"]}}
                    for c in cells]
        _s3.put_object(Bucket=BUCKET, Key=f"grid/{key}.geojson", ContentType="application/geo+json",
                       Body=json.dumps({"type": "FeatureCollection", "features": features}))
        counts = {b: sum(1 for c in cells if c["band"] == b) for b in ("green", "yellow", "red")}
        summary[key] = dict(counts, maxWbgt=max(c["wbgt"] for c in cells), cells=len(cells))
    return summary


def update_index(mode, summary, date=None):
    try:
        index = json.loads(_s3.get_object(Bucket=BUCKET, Key="grid/index.json")["Body"].read())
    except Exception:
        index = {"forecast": {}, "replay": {}}
    if mode == "forecast":
        index["forecast"] = summary
    else:
        index.setdefault("replay", {})[date] = summary
    index["updatedAt"] = int(time.time())
    _s3.put_object(Bucket=BUCKET, Key="grid/index.json", Body=json.dumps(index), ContentType="application/json")
    return index


def run_forecast():
    now_ms = int(time.time() * 1000)
    wanted = {}
    for h in range(0, 7):
        k = core.local_hour_key(now_ms + h * 3600000, CFG)
        wanted[k] = k
    cells = core.grid_cells(BBOX, CFG["cellDeg"])
    by_hour = compute(fetch(cells, FORECAST_URL, {"forecast_days": 2}), wanted)
    summary = store(by_hour, ttl_days=3)
    update_index("forecast", summary)
    return {"mode": "forecast", "cells": len(cells), "hours": sorted(summary), "summary": summary}


def run_replay(date):
    wanted = {f"{date}T{h:02d}": f"R{date}T{h:02d}" for h in range(8, 20)}
    cells = core.grid_cells(BBOX, CFG["cellDeg"])
    by_hour = compute(fetch(cells, ARCHIVE_URL, {"start_date": date, "end_date": date}), wanted)
    summary = store(by_hour, ttl_days=60)
    update_index("replay", summary, date)
    return {"mode": "replay", "date": date, "cells": len(cells), "summary": summary}


def run_scan(start, end):
    s, w, n, e = BBOX
    data = http_json(ARCHIVE_URL, {"latitude": f"{(s + n) / 2:.4f}", "longitude": f"{(w + e) / 2:.4f}",
                                   "hourly": HOURLY, "timezone": TZ, "start_date": start, "end_date": end})
    h, days = data["hourly"], {}
    for i, t in enumerate(h["time"]):
        if h["temperature_2m"][i] is None:
            continue
        w = core.wbgt_outdoor(h["temperature_2m"][i], h["relative_humidity_2m"][i], h["shortwave_radiation"][i] or 0, CFG)
        d = days.setdefault(t[:10], {"date": t[:10], "maxWbgt": 0, "maxTempC": 0, "redHours": 0})
        d["maxWbgt"] = max(d["maxWbgt"], w)
        d["maxTempC"] = max(d["maxTempC"], h["temperature_2m"][i])
        d["redHours"] += core.band_for(w, CFG) == "red"
    top = sorted(days.values(), key=lambda d: (d["redHours"], d["maxWbgt"]), reverse=True)[:5]
    return {"mode": "scan", "hottest": top}


def handler(event, _context):
    event = event or {}
    mode = event.get("mode", "forecast")
    if mode == "replay":
        return run_replay(event.get("date") or os.environ.get("REPLAY_DATE", "2024-05-03"))
    if mode == "scan":
        return run_scan(event.get("from", "2024-04-01"), event.get("to", "2024-06-15"))
    return run_forecast()
