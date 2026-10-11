"""Pure heat-safety logic: WBGT, grid cells, heat dose, breaks and nudges.

No AWS calls live here, so every rule can be unit-tested offline.
All thresholds come from config.json.
"""
import json
import math
import os
from datetime import datetime, timedelta, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))


def load_config(path=None):
    with open(path or os.path.join(_HERE, "config.json"), encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------- geometry

EARTH_M = 6371000.0


def haversine_m(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_M * math.asin(math.sqrt(a))


def bearing_deg(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def angle_diff(a, b):
    d = abs(a - b) % 360
    return 360 - d if d > 180 else d


def compass(bearing):
    names = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    return names[int((bearing + 22.5) // 45) % 8]


# ---------------------------------------------------------------- grid cells

def cell_id(lat, lon, cell_deg=0.02):
    return f"{round(lat / cell_deg)}_{round(lon / cell_deg)}"


def cell_center(cid, cell_deg=0.02):
    a, b = cid.split("_")
    return int(a) * cell_deg, int(b) * cell_deg


def neighbour_cells(lat, lon, cell_deg=0.02):
    i, j = round(lat / cell_deg), round(lon / cell_deg)
    return [f"{i + di}_{j + dj}" for di in (-1, 0, 1) for dj in (-1, 0, 1)]


def grid_cells(bbox, cell_deg=0.02):
    """bbox = (south, west, north, east). Returns cell ids covering it."""
    s, w, n, e = bbox
    out = []
    for i in range(round(s / cell_deg), round(n / cell_deg) + 1):
        for j in range(round(w / cell_deg), round(e / cell_deg) + 1):
            out.append(f"{i}_{j}")
    return out


def clamp_to_bbox(lat, lon, bbox):
    s, w, n, e = bbox
    return min(max(lat, s), n), min(max(lon, w), e)


def cell_polygon(cid, cell_deg=0.02):
    lat, lon = cell_center(cid, cell_deg)
    h = cell_deg / 2
    return [[lon - h, lat - h], [lon + h, lat - h], [lon + h, lat + h], [lon - h, lat + h], [lon - h, lat - h]]


# ---------------------------------------------------------------- heat

def vapour_pressure_hpa(temp_c, rh):
    return (rh / 100.0) * 6.105 * math.exp(17.27 * temp_c / (237.7 + temp_c))


def wbgt_shade(temp_c, rh):
    """Australian Bureau of Meteorology approximation (shade, light wind)."""
    return 0.567 * temp_c + 0.393 * vapour_pressure_hpa(temp_c, rh) + 3.94


def wbgt_outdoor(temp_c, rh, shortwave_wm2, cfg):
    """Shade WBGT plus a capped direct-sun allowance. Approximate by design."""
    sun = cfg["sunAdjustMaxC"] * max(0.0, min((shortwave_wm2 or 0) / 1000.0, 1.0))
    return round(wbgt_shade(temp_c, rh) + sun, 1)


def band_for(wbgt, cfg):
    if wbgt is None:
        return "unknown"
    if wbgt >= cfg["bands"]["redFromC"]:
        return "red"
    if wbgt >= cfg["bands"]["yellowFromC"]:
        return "yellow"
    return "green"


# ---------------------------------------------------------------- time keys

def local_hour_key(ts_ms, cfg):
    tz = timezone(timedelta(minutes=cfg["timezoneOffsetMin"]))
    return datetime.fromtimestamp(ts_ms / 1000, tz).strftime("%Y-%m-%dT%H")


def replay_hour_key(replay_date, start_ts_ms, ts_ms, cfg):
    hours = int(max(0, ts_ms - start_ts_ms) // 3600000)
    hour = min(cfg["replayStartHour"] + hours, cfg["replayLastHour"])
    return f"R{replay_date}T{hour:02d}"


# ---------------------------------------------------------------- points

def annotate_points(points, lat, lon):
    """Adds distance (m) and bearing from the rider; sorted nearest first."""
    out = []
    for p in points:
        q = dict(p)
        q["dist"] = round(haversine_m(lat, lon, p["lat"], p["lon"]))
        q["bearing"] = round(bearing_deg(lat, lon, p["lat"], p["lon"]))
        q["compass"] = compass(q["bearing"])
        out.append(q)
    out.sort(key=lambda q: q["dist"])
    return out


# ---------------------------------------------------------------- dose engine

def new_state(ts, lat, lon):
    return {
        "dose": 0.0, "redMin": 0.0, "yellowMin": 0.0, "greenMin": 0.0,
        "lastTs": None, "lastLat": None, "lastLon": None,
        "anchorLat": lat, "anchorLon": lon, "stationarySince": ts,
        "stationaryMin": 0.0, "moving": True, "heading": None,
        "onBreak": False, "breakCount": 0, "breakMin": 0.0,
        "lastNudgeTs": 0, "lastCriticalTs": 0,
        "peakWbgt": None, "peakLat": None, "peakLon": None,
    }


def step(state, ping, wbgt, band, points, cfg):
    """Advance the rider's state by one GPS ping. Returns (state, events)."""
    st = dict(state)
    events = []
    ts, lat, lon = ping["ts"], ping["lat"], ping["lon"]

    dt = 0.0
    if st["lastTs"] is not None:
        dt = max(0.0, min((ts - st["lastTs"]) / 60000.0, cfg["maxGapMin"]))

    if st["lastLat"] is not None:
        moved = haversine_m(st["lastLat"], st["lastLon"], lat, lon)
        if moved >= cfg["headingMinMoveM"]:
            st["heading"] = bearing_deg(st["lastLat"], st["lastLon"], lat, lon)

    if haversine_m(st["anchorLat"], st["anchorLon"], lat, lon) > cfg["stationaryRadiusM"]:
        st["anchorLat"], st["anchorLon"], st["stationarySince"] = lat, lon, ts
    st["stationaryMin"] = (ts - st["stationarySince"]) / 60000.0
    st["moving"] = st["stationaryMin"] < cfg["movingIfStationaryUnderMin"]

    rest_point = None
    for p in points:
        if p.get("kind") in cfg["pointKinds"]["rest"]:
            d = haversine_m(st["anchorLat"], st["anchorLon"], p["lat"], p["lon"])
            if d <= cfg["breakRadiusM"]:
                rest_point = p
                break
    on_break = st["stationaryMin"] >= cfg["breakAfterMin"] and rest_point is not None

    if on_break and not st["onBreak"]:
        st["breakCount"] += 1
        events.append("break-start")
    if not on_break and st["onBreak"]:
        events.append("break-end")

    if on_break:
        st["dose"] = max(0.0, st["dose"] - dt * cfg["recoveryPerMin"])
        st["breakMin"] += dt
    else:
        st["dose"] += dt * cfg["doseWeightPerMin"].get(band, 0)
        key = {"red": "redMin", "yellow": "yellowMin", "green": "greenMin"}.get(band)
        if key:
            st[key] += dt
    st["onBreak"] = on_break

    if wbgt is not None and (st["peakWbgt"] is None or wbgt > st["peakWbgt"]):
        st["peakWbgt"], st["peakLat"], st["peakLon"] = wbgt, lat, lon

    st["lastTs"], st["lastLat"], st["lastLon"] = ts, lat, lon
    st["dose"] = round(st["dose"], 2)
    return st, events


def _nearest(points, kinds, max_dist, heading=None, max_angle=None):
    for p in points:  # already sorted nearest first
        if p.get("kind") not in kinds or p["dist"] > max_dist:
            continue
        if heading is not None and angle_diff(heading, p["bearing"]) > max_angle:
            continue
        return p
    return None


def _nudge(nudge_id, point):
    n = {"id": nudge_id, "point": None}
    if point:
        n["point"] = {k: point.get(k) for k in ("pointId", "lat", "lon", "kind", "name", "dist", "bearing", "compass")}
    return n


def choose_nudge(state, events, ping, points, cfg, unwell=False):
    """Pick at most one spoken nudge. `points` must be annotated from the rider.
    Returns (state, nudge or None)."""
    st = dict(state)
    ts = ping["ts"]
    rest_kinds = cfg["pointKinds"]["rest"]

    if unwell:
        st["lastCriticalTs"] = st["lastNudgeTs"] = ts
        return st, _nudge("unwell", _nearest(points, rest_kinds, 10 ** 9))

    if "break-start" in events:
        return st, _nudge("break-logged", None)

    if (st["dose"] >= cfg["criticalDose"] and not st["onBreak"]
            and ts - st["lastCriticalTs"] >= cfg["criticalCooldownMin"] * 60000):
        st["lastCriticalTs"] = st["lastNudgeTs"] = ts
        return st, _nudge("critical", _nearest(points, rest_kinds, 10 ** 9))

    if ts - st["lastNudgeTs"] < cfg["nudgeCooldownMin"] * 60000 or st["onBreak"]:
        return st, None

    if (ping.get("online", True) and st["stationaryMin"] >= cfg["idleAfterMin"]
            and st["dose"] >= cfg["idleDose"]):
        p = _nearest(points, cfg["pointKinds"]["idle"], cfg["idleRadiusM"])
        if p:
            st["lastNudgeTs"] = ts
            return st, _nudge("idle", p)

    if st["moving"] and st["heading"] is not None and st["dose"] >= cfg["routeDose"]:
        p = _nearest(points, cfg["pointKinds"]["route"], cfg["routeRadiusM"],
                     heading=st["heading"], max_angle=cfg["routeMaxAngleDeg"])
        if p:
            st["lastNudgeTs"] = ts
            return st, _nudge("on-route", p)

    return st, None


def gauge(state, cfg):
    """0-100 for the UI gauge, plus a level word."""
    pct = min(100, round(100 * state["dose"] / cfg["criticalDose"]))
    level = "low" if pct < 25 else "moderate" if pct < 50 else "high" if pct < 100 else "critical"
    return pct, level
