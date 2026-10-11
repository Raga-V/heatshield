"""HTTP API for the rider app (one Lambda, simple path router)."""
import base64
import json
import os
import time
import uuid
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key

import core

CFG = core.load_config()
BBOX = tuple(float(x) for x in os.environ.get("BBOX", "17.30,78.30,17.56,78.60").split(","))
CITY = os.environ.get("CITY_NAME", "hyderabad")
REPLAY_DATE = os.environ.get("REPLAY_DATE", "2024-05-03")
BUCKET = os.environ.get("DATA_BUCKET", "")

_ddb = boto3.resource("dynamodb")
_s3 = boto3.client("s3")
_polly = boto3.client("polly")
GRID = _ddb.Table(os.environ.get("GRID_TABLE", "grid"))
SHIFTS = _ddb.Table(os.environ.get("SHIFTS_TABLE", "shifts"))
PINGS = _ddb.Table(os.environ.get("PINGS_TABLE", "pings"))
POINTS = _ddb.Table(os.environ.get("POINTS_TABLE", "points"))

CLIPS = {
    "en": {
        "shift-start": "Shift started. Stay safe in the heat.",
        "idle": "You have been in the heat a while. Shade and water are close by while you wait.",
        "on-route": "Water point just ahead on your route.",
        "critical": "Your heat level is very high. Stop now and rest at the nearest point.",
        "break-logged": "Break logged. Good job.",
        "unwell": "Rest in shade, drink water, and call for help if you feel worse.",
    },
    "hi": {
        "shift-start": "शिफ्ट शुरू हो गई है। गर्मी में अपना ध्यान रखें।",
        "idle": "आप काफ़ी देर से गर्मी में हैं। ऑर्डर का इंतज़ार करते हुए पास में छाया और पानी है।",
        "on-route": "आपके रास्ते में आगे पानी मिलेगा।",
        "critical": "गर्मी का असर बहुत ज़्यादा है। अभी रुकिए और पास की जगह पर आराम कीजिए।",
        "break-logged": "ब्रेक दर्ज हो गया। बहुत बढ़िया।",
        "unwell": "छाया में बैठिए, पानी पीजिए, और तबीयत बिगड़े तो मदद बुलाइए।",
    },
}

FIRST_AID = {
    "en": ["Move to shade and sit down", "Sip water slowly", "Loosen helmet and clothes, wet your skin",
           "Call 108 if you feel confused, stop sweating or faint"],
    "hi": ["छाया में जाकर बैठ जाइए", "धीरे-धीरे पानी पीजिए", "हेलमेट और कपड़े ढीले करें, त्वचा गीली करें",
           "उलझन हो, पसीना बंद हो या चक्कर आए तो 108 पर कॉल करें"],
}


# ---------------------------------------------------------------- helpers

def _dec(o):
    if isinstance(o, Decimal):
        return int(o) if o == int(o) else float(o)
    raise TypeError


def to_ddb(obj):
    return json.loads(json.dumps(obj), parse_float=Decimal)


def from_ddb(obj):
    return json.loads(json.dumps(obj, default=_dec))


def resp(code, body):
    return {
        "statusCode": code,
        "headers": {"content-type": "application/json", "access-control-allow-origin": "*",
                    "access-control-allow-headers": "content-type",
                    "access-control-allow-methods": "GET,POST,OPTIONS"},
        "body": json.dumps(body, default=_dec, ensure_ascii=False),
    }


class BadRequest(Exception):
    pass


def need(body, *keys):
    missing = [k for k in keys if body.get(k) in (None, "")]
    if missing:
        raise BadRequest("missing: " + ", ".join(missing))
    return [body[k] for k in keys]


# ---------------------------------------------------------------- grid

def grid_cell(cid, hour_key):
    item = GRID.get_item(Key={"cellId": cid, "hourKey": hour_key}).get("Item")
    if item:
        return float(item["wbgt"]), item["band"]
    return None, "unknown"


def lookup_heat(lat, lon, hour_key):
    lat, lon = core.clamp_to_bbox(lat, lon, BBOX)
    cid = core.cell_id(lat, lon, CFG["cellDeg"])
    wbgt, band = grid_cell(cid, hour_key)
    if wbgt is None and not hour_key.startswith("R"):
        # forecast for this hour not written yet: fall back one hour
        prev = core.local_hour_key(_key_to_ms(hour_key) - 3600000, CFG)
        wbgt, band = grid_cell(cid, prev)
    return cid, wbgt, band


def _key_to_ms(hour_key):
    from datetime import datetime, timedelta, timezone
    tz = timezone(timedelta(minutes=CFG["timezoneOffsetMin"]))
    return int(datetime.strptime(hour_key, "%Y-%m-%dT%H").replace(tzinfo=tz).timestamp() * 1000)


def hour_key_for_shift(shift, ts):
    if shift.get("replayDate"):
        return core.replay_hour_key(shift["replayDate"], int(shift["startTs"]), ts, CFG)
    return core.local_hour_key(ts, CFG)


def s3_json(key, default=None):
    try:
        obj = _s3.get_object(Bucket=BUCKET, Key=key)
        return json.loads(obj["Body"].read())
    except Exception:
        return default


# ---------------------------------------------------------------- points

def near_points(lat, lon, kind=None, limit=8):
    found = {}
    for cid in core.neighbour_cells(lat, lon, CFG["cellDeg"]):
        r = POINTS.query(IndexName="byCell", KeyConditionExpression=Key("cellId").eq(cid))
        for it in r.get("Items", []):
            found[it["pointId"]] = from_ddb(it)
    pts = core.annotate_points(list(found.values()), lat, lon)
    if kind:
        pts = [p for p in pts if p["kind"] == kind]
    return pts[:limit]


# ---------------------------------------------------------------- routes

def route_config(_b, _q):
    return {"city": CITY, "bbox": BBOX, "replayDate": REPLAY_DATE, "bands": CFG["bands"],
            "criticalDose": CFG["criticalDose"], "timezoneOffsetMin": CFG["timezoneOffsetMin"],
            "replayStartHour": CFG["replayStartHour"]}


def route_grid_index(_b, _q):
    return s3_json("grid/index.json", {"forecast": [], "replay": {}})


def route_grid(_b, q):
    hour = q.get("hour") or core.local_hour_key(int(time.time() * 1000), CFG)
    data = s3_json(f"grid/{hour}.geojson")
    if data is None:
        return {"type": "FeatureCollection", "features": [], "hourKey": hour, "missing": True}
    data["hourKey"] = hour
    return data


def route_shift_start(b, _q):
    rider_id, = need(b, "riderId")
    ts = int(b.get("ts") or time.time() * 1000)
    shift = {
        "riderId": rider_id, "shiftId": str(ts) + "-" + uuid.uuid4().hex[:6], "status": "active",
        "startTs": ts, "lang": b.get("lang", "en"), "vehicle": b.get("vehicle", "bike"),
        "replayDate": b.get("replayDate") or None, "state": None,
    }
    SHIFTS.put_item(Item=to_ddb(shift))
    return {"shiftId": shift["shiftId"], "startTs": ts, "replayDate": shift["replayDate"]}


def _load_shift(b):
    rider_id, shift_id = need(b, "riderId", "shiftId")
    item = SHIFTS.get_item(Key={"riderId": rider_id, "shiftId": shift_id}).get("Item")
    if not item:
        raise BadRequest("unknown shift")
    return from_ddb(item)


def _public(st, extra):
    pct, level = core.gauge(st, CFG)
    out = {"dose": st["dose"], "gauge": pct, "level": level, "redMin": round(st["redMin"]),
           "yellowMin": round(st["yellowMin"]), "breakCount": st["breakCount"], "onBreak": st["onBreak"]}
    out.update(extra)
    return out


def route_ping(b, _q):
    shift = _load_shift(b)
    if shift["status"] != "active":
        raise BadRequest("shift ended")
    lat, lon = float(b["lat"]), float(b["lon"])
    ts = int(b.get("ts") or time.time() * 1000)
    ping = {"ts": ts, "lat": lat, "lon": lon, "online": bool(b.get("online", True))}
    st = shift.get("state") or core.new_state(ts, lat, lon)

    if float(b.get("accuracyM") or 0) > CFG["maxAccuracyM"]:
        return _public(st, {"ignored": "low GPS accuracy", "nudge": None})

    hour_key = hour_key_for_shift(shift, ts)
    cid, wbgt, band = lookup_heat(lat, lon, hour_key)
    points = near_points(lat, lon, limit=20)
    st, events = core.step(st, ping, wbgt, band, points, CFG)
    st, nudge = core.choose_nudge(st, events, ping, points, CFG)

    SHIFTS.update_item(Key={"riderId": shift["riderId"], "shiftId": shift["shiftId"]},
                       UpdateExpression="SET #s = :s", ExpressionAttributeNames={"#s": "state"},
                       ExpressionAttributeValues={":s": to_ddb(st)})
    PINGS.put_item(Item=to_ddb({"shiftId": shift["shiftId"], "ts": ts, "lat": lat, "lon": lon,
                                "cellId": cid, "hourKey": hour_key, "wbgt": wbgt, "band": band,
                                "dose": st["dose"], "nudge": nudge["id"] if nudge else None,
                                "expiresAt": int(time.time()) + 30 * 86400}))
    return _public(st, {"wbgt": wbgt, "band": band, "hourKey": hour_key, "events": events, "nudge": nudge})


def route_unwell(b, _q):
    shift = _load_shift(b)
    lat, lon = float(b["lat"]), float(b["lon"])
    ts = int(b.get("ts") or time.time() * 1000)
    st = shift.get("state") or core.new_state(ts, lat, lon)
    points = near_points(lat, lon, limit=20)
    st, nudge = core.choose_nudge(st, [], {"ts": ts, "lat": lat, "lon": lon}, points, CFG, unwell=True)
    SHIFTS.update_item(Key={"riderId": shift["riderId"], "shiftId": shift["shiftId"]},
                       UpdateExpression="SET #s = :s, unwellCount = :u",
                       ExpressionAttributeNames={"#s": "state"},
                       ExpressionAttributeValues={":s": to_ddb(st), ":u": int(shift.get("unwellCount", 0)) + 1})
    return _public(st, {"nudge": nudge, "firstAid": FIRST_AID.get(shift.get("lang", "en"), FIRST_AID["en"])})


def route_shift_end(b, _q):
    shift = _load_shift(b)
    items, kwargs = [], {"KeyConditionExpression": Key("shiftId").eq(shift["shiftId"])}
    while True:
        r = PINGS.query(**kwargs)
        items += [from_ddb(i) for i in r.get("Items", [])]
        if "LastEvaluatedKey" not in r:
            break
        kwargs["ExclusiveStartKey"] = r["LastEvaluatedKey"]
    items.sort(key=lambda p: p["ts"])
    st = shift.get("state") or core.new_state(int(time.time() * 1000), 0, 0)
    end_ts = items[-1]["ts"] if items else int(shift["startTs"])
    summary = {
        "minutes": round((end_ts - int(shift["startTs"])) / 60000),
        "redMin": round(st["redMin"]), "yellowMin": round(st["yellowMin"]), "greenMin": round(st["greenMin"]),
        "breaks": st["breakCount"], "breakMin": round(st["breakMin"]), "finalDose": st["dose"],
        "peakWbgt": st["peakWbgt"], "peak": [st["peakLon"], st["peakLat"]] if st["peakLat"] else None,
        "nudges": [p["nudge"] for p in items if p.get("nudge")],
        "path": [[p["lon"], p["lat"], p.get("band", "unknown")] for p in items],
    }
    SHIFTS.update_item(Key={"riderId": shift["riderId"], "shiftId": shift["shiftId"]},
                       UpdateExpression="SET #st = :e, summary = :s",
                       ExpressionAttributeNames={"#st": "status"},
                       ExpressionAttributeValues={":e": "ended", ":s": to_ddb({k: v for k, v in summary.items() if k != "path"})})
    return summary


def route_points_near(_b, q):
    lat, lon = float(q["lat"]), float(q["lon"])
    return {"points": near_points(lat, lon, q.get("kind"), int(q.get("limit", 8)))}


def route_points_add(b, _q):
    lat, lon, kind, rider_id = need(b, "lat", "lon", "kind", "riderId")
    if kind not in ("water", "shade", "rest"):
        raise BadRequest("kind must be water, shade or rest")
    lat, lon = float(lat), float(lon)
    item = {"pointId": "rider-" + uuid.uuid4().hex[:10], "lat": lat, "lon": lon, "kind": kind,
            "name": b.get("name") or "", "source": "rider", "verified": False,
            "addedBy": rider_id, "confirmedBy": [], "cellId": core.cell_id(lat, lon, CFG["cellDeg"])}
    POINTS.put_item(Item=to_ddb(item))
    return {"pointId": item["pointId"], "verified": False}


def route_points_verify(b, _q):
    point_id, rider_id = need(b, "pointId", "riderId")
    item = POINTS.get_item(Key={"pointId": point_id}).get("Item")
    if not item:
        raise BadRequest("unknown point")
    item = from_ddb(item)
    if rider_id != item.get("addedBy") and rider_id not in item.get("confirmedBy", []):
        item.setdefault("confirmedBy", []).append(rider_id)
        item["verified"] = True
        POINTS.put_item(Item=to_ddb(item))
    return {"pointId": point_id, "verified": item.get("verified", False)}


def route_audio(_b, q):
    clip, lang = q.get("clip", ""), q.get("lang", "en")
    if lang not in CLIPS or clip not in CLIPS[lang]:
        raise BadRequest("unknown clip")
    key = f"audio/{lang}/{clip}.mp3"
    try:
        data = _s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()
    except Exception:
        data = synthesize(CLIPS[lang][clip], lang)
        _s3.put_object(Bucket=BUCKET, Key=key, Body=data, ContentType="audio/mpeg")
    return {"clip": clip, "lang": lang, "text": CLIPS[lang][clip], "mime": "audio/mpeg",
            "b64": base64.b64encode(data).decode()}


def synthesize(text, lang):
    code = "hi-IN" if lang == "hi" else "en-IN"
    try:
        r = _polly.synthesize_speech(Text=text, OutputFormat="mp3", VoiceId="Kajal", Engine="neural", LanguageCode=code)
    except Exception:
        r = _polly.synthesize_speech(Text=text, OutputFormat="mp3", VoiceId="Aditi", Engine="standard", LanguageCode=code)
    return r["AudioStream"].read()


ROUTES = {
    ("GET", "/config"): route_config,
    ("GET", "/grid/index"): route_grid_index,
    ("GET", "/grid"): route_grid,
    ("POST", "/shifts/start"): route_shift_start,
    ("POST", "/shifts/ping"): route_ping,
    ("POST", "/shifts/unwell"): route_unwell,
    ("POST", "/shifts/end"): route_shift_end,
    ("GET", "/points/near"): route_points_near,
    ("POST", "/points"): route_points_add,
    ("POST", "/points/verify"): route_points_verify,
    ("GET", "/audio"): route_audio,
}


def handler(event, _context):
    http = event.get("requestContext", {}).get("http", {})
    method, path = http.get("method", "GET"), event.get("rawPath", "/")
    if method == "OPTIONS":
        return resp(204, {})
    fn = ROUTES.get((method, path.rstrip("/") or "/"))
    if not fn:
        return resp(404, {"error": f"no route {method} {path}"})
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode()
    try:
        body = json.loads(raw) if method == "POST" else {}
        return resp(200, fn(body, event.get("queryStringParameters") or {}))
    except (BadRequest, KeyError, ValueError) as e:
        return resp(400, {"error": str(e)})
