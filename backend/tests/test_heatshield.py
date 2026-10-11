import json
import math
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "src"))

import fake_aws  # noqa: E402

fake_aws.install()

import core  # noqa: E402

CFG = core.load_config()
P = (17.4400, 78.4500)  # a water point in Hyderabad


def east(lat, lon, metres):
    return lat, lon + metres / (111320 * math.cos(math.radians(lat)))


def demo_track():
    """Same scripted ride as the web app's demo mode: (minute, metres west of P, online)."""
    out, x = [], 1500.0
    for m in range(0, 16):                       # A: ride back and forth far from P
        x = 1500 - (m % 6) * 200
        out.append((m, x))
    for m in range(16, 22):                      # B: wait for an order 250 m from P
        out.append((m, 250))
    for m in range(22, 34):                      # C: ride away, back and forth
        out.append((m, 1500 - (m % 4) * 200))
    for i, m in enumerate(range(34, 40)):        # D: ride east toward P
        out.append((m, 1500 - i * 300))
    for m in range(40, 53):                      # E: rest at the water point
        out.append((m, 15))
    for i, m in enumerate(range(53, 60)):        # F: back to work
        out.append((m, -200 - i * 250))
    return [(m, *east(P[0], P[1], -x)) for m, x in out]


class CoreTests(unittest.TestCase):
    def test_wbgt_hot_dry_and_humid(self):
        dry = core.wbgt_outdoor(44, 15, 900, CFG)   # Hyderabad May afternoon
        humid = core.wbgt_outdoor(36, 60, 900, CFG)  # coastal monsoon-edge day
        self.assertEqual(core.band_for(dry, CFG), "red")
        self.assertGreater(humid, dry - 3)
        self.assertEqual(core.band_for(core.wbgt_outdoor(24, 50, 0, CFG), CFG), "green")

    def test_cells_round_trip(self):
        cid = core.cell_id(17.4401, 78.4499)
        lat, lon = core.cell_center(cid)
        self.assertLess(core.haversine_m(lat, lon, 17.4401, 78.4499), 1600)
        self.assertEqual(len(core.neighbour_cells(17.44, 78.45)), 9)

    def test_dose_accrues_only_in_heat_and_caps_gaps(self):
        st = core.new_state(0, *P)
        st, _ = core.step(st, {"ts": 0, "lat": P[0], "lon": P[1]}, 33, "red", [], CFG)
        st, _ = core.step(st, {"ts": 60000, "lat": P[0], "lon": P[1] + 0.003}, 33, "red", [], CFG)
        self.assertEqual(st["dose"], 2)
        st, _ = core.step(st, {"ts": 60000 * 30, "lat": P[0], "lon": P[1] + 0.006}, 33, "red", [], CFG)
        self.assertEqual(st["dose"], 2 + 2 * CFG["maxGapMin"])  # phone slept: capped
        st, _ = core.step(st, {"ts": 60000 * 31, "lat": P[0], "lon": P[1]}, 25, "green", [], CFG)
        self.assertEqual(st["dose"], 6)

    def test_break_needs_a_rest_point(self):
        st = core.new_state(0, *P)
        far = [{"pointId": "x", "lat": P[0] + 0.01, "lon": P[1], "kind": "water"}]
        for m in range(12):
            st, ev = core.step(st, {"ts": m * 60000, "lat": P[0], "lon": P[1]}, 33, "red", far, CFG)
        self.assertFalse(st["onBreak"])
        near = [{"pointId": "w", "lat": P[0], "lon": P[1], "kind": "water"}]
        st, ev = core.step(st, {"ts": 12 * 60000, "lat": P[0], "lon": P[1]}, 33, "red", near, CFG)
        self.assertTrue(st["onBreak"])
        self.assertIn("break-start", ev)

    def test_on_route_needs_point_ahead(self):
        st = core.new_state(0, *P)
        st.update(dose=70, heading=90, moving=True, stationaryMin=0)
        behind = core.annotate_points([{"pointId": "b", "lat": P[0], "lon": P[1] - 0.003, "kind": "water"}], *P)
        ahead = core.annotate_points([{"pointId": "a", "lat": P[0], "lon": P[1] + 0.003, "kind": "water"}], *P)
        _, n = core.choose_nudge(st, [], {"ts": 10 ** 7, "lat": P[0], "lon": P[1]}, behind, CFG)
        self.assertIsNone(n)
        _, n = core.choose_nudge(st, [], {"ts": 10 ** 7, "lat": P[0], "lon": P[1]}, ahead, CFG)
        self.assertEqual(n["id"], "on-route")

    def test_critical_and_unwell(self):
        st = core.new_state(0, *P)
        st.update(dose=125)
        st, n = core.choose_nudge(st, [], {"ts": 10 ** 7, "lat": P[0], "lon": P[1]}, [], CFG)
        self.assertEqual(n["id"], "critical")
        _, n = core.choose_nudge(st, [], {"ts": 10 ** 7 + 60000, "lat": P[0], "lon": P[1]}, [], CFG)
        self.assertIsNone(n)  # cooldown
        _, n = core.choose_nudge(st, [], {"ts": 10 ** 7 + 60000, "lat": P[0], "lon": P[1]}, [], CFG, unwell=True)
        self.assertEqual(n["id"], "unwell")


class ApiEndToEnd(unittest.TestCase):
    def setUp(self):
        import api
        self.api = api
        fake_aws.TABLES.clear()
        fake_aws.S3.clear()
        # a red replay hour across the whole city
        for cid in core.grid_cells(api.BBOX):
            for h in range(13, 19):
                api.GRID.put_item(Item={"cellId": cid, "hourKey": f"R2024-05-03T{h:02d}", "wbgt": 33.5, "band": "red"})
        api.POINTS.put_item(Item={"pointId": "osm-node-1", "lat": P[0], "lon": P[1], "kind": "water",
                                  "name": "Water ATM", "cellId": core.cell_id(*P)})

    def call(self, method, path, body=None, qs=None):
        ev = {"requestContext": {"http": {"method": method}}, "rawPath": path,
              "body": json.dumps(body) if body else None, "queryStringParameters": qs}
        r = self.api.handler(ev, None)
        return r["statusCode"], json.loads(r["body"])

    def test_demo_ride_fires_every_nudge_in_order(self):
        t0 = 1_760_000_000_000
        code, s = self.call("POST", "/shifts/start", {"riderId": "r1", "lang": "hi", "ts": t0, "replayDate": "2024-05-03"})
        self.assertEqual(code, 200)
        nudges = []
        for m, lat, lon in demo_track():
            code, r = self.call("POST", "/shifts/ping", {"riderId": "r1", "shiftId": s["shiftId"], "lat": lat,
                                                         "lon": lon, "ts": t0 + m * 60000, "online": True})
            self.assertEqual(code, 200, r)
            if r["nudge"]:
                nudges.append((m, r["nudge"]["id"]))
        ids = [n for _, n in nudges]
        self.assertEqual(ids, ["idle", "on-route", "break-logged"], nudges)

        code, u = self.call("POST", "/shifts/unwell", {"riderId": "r1", "shiftId": s["shiftId"], "lat": P[0], "lon": P[1]})
        self.assertEqual(u["nudge"]["id"], "unwell")
        self.assertEqual(u["nudge"]["point"]["name"], "Water ATM")
        self.assertEqual(len(u["firstAid"]), 4)

        code, summary = self.call("POST", "/shifts/end", {"riderId": "r1", "shiftId": s["shiftId"]})
        self.assertEqual(summary["breaks"], 1)
        self.assertGreater(summary["redMin"], 40)
        self.assertEqual(len(summary["path"]), len(demo_track()))
        print("\n  demo nudges:", nudges, "| final dose:", summary["finalDose"], "| red min:", summary["redMin"])

    def test_points_audio_and_errors(self):
        code, r = self.call("GET", "/points/near", qs={"lat": str(P[0]), "lon": str(P[1] + 0.002)})
        self.assertEqual(r["points"][0]["compass"], "W")
        code, r = self.call("POST", "/points", {"lat": 17.45, "lon": 78.46, "kind": "shade", "riderId": "r2"})
        pid = r["pointId"]
        self.assertEqual(self.call("POST", "/points/verify", {"pointId": pid, "riderId": "r2"})[1]["verified"], False)
        self.assertEqual(self.call("POST", "/points/verify", {"pointId": pid, "riderId": "r3"})[1]["verified"], True)
        code, a = self.call("GET", "/audio", qs={"clip": "idle", "lang": "hi"})
        self.assertEqual(code, 200)
        self.assertIn("audio/hi/idle.mp3", fake_aws.S3)
        self.assertEqual(self.call("GET", "/audio", qs={"clip": "nope"})[0], 400)
        self.assertEqual(self.call("POST", "/shifts/ping", {"riderId": "x"})[0], 400)
        self.assertEqual(self.call("GET", "/nope")[0], 404)


class GridTests(unittest.TestCase):
    def test_compute_and_store(self):
        import grid
        fake_aws.S3.clear()
        cells = core.grid_cells(grid.BBOX)[:3]
        hourly = {"time": ["2024-05-03T14:00", "2024-05-03T15:00"], "temperature_2m": [44.1, 43.0],
                  "relative_humidity_2m": [14, 16], "wind_speed_10m": [3, 4], "shortwave_radiation": [850, 700]}
        by_hour = grid.compute({c: hourly for c in cells}, {"2024-05-03T14": "R2024-05-03T14"})
        self.assertEqual(list(by_hour), ["R2024-05-03T14"])
        summary = grid.store(by_hour, 1)
        grid.update_index("replay", summary, "2024-05-03")
        self.assertIn("grid/R2024-05-03T14.geojson", fake_aws.S3)
        idx = json.loads(fake_aws.S3["grid/index.json"])
        self.assertEqual(idx["replay"]["2024-05-03"]["R2024-05-03T14"]["red"], 3)


class SeedTests(unittest.TestCase):
    def test_overpass_down_still_writes_extra_points(self):
        import seed
        fake_aws.TABLES.clear()
        seed.OVERPASS = ["http://127.0.0.1:9/unreachable"]
        r = seed.handler({"extra": [{"lat": 17.44, "lon": 78.45, "kind": "water", "name": "Test tap"}]}, None)
        self.assertEqual(r["seeded"], 1)
        self.assertIn("osmError", r)
        self.assertIn(("manual-0",), seed.POINTS.items)


if __name__ == "__main__":
    unittest.main(verbosity=2)
