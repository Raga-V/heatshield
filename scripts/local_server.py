"""Run the API and the web app on your laptop with in-memory fake AWS (no account needed).

    python3 scripts/local_server.py          then open http://localhost:8787/?demo=1&replay=1

For UI work only. The real demo must run on the deployed AWS stack.
"""
import json
import math
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "backend", "src"), os.path.join(ROOT, "backend", "tests")]
import fake_aws  # noqa: E402

fake_aws.install()
import api  # noqa: E402
import core  # noqa: E402
import grid  # noqa: E402

PORT = int(os.environ.get("PORT", 8787))
WEB = os.path.join(ROOT, "web")


def seed():
    """Synthetic red afternoon + a few points around central Hyderabad."""
    cfg = core.load_config()
    date = api.REPLAY_DATE
    by_hour = {}
    for cid in core.grid_cells(api.BBOX):
        lat, lon = core.cell_center(cid)
        for h in range(8, 20):
            d = math.hypot(lat - 17.40, (lon - 78.48) * 0.95)
            temp = 30 + 13 * math.sin(math.pi * (h - 6) / 14) - 35 * d
            w = core.wbgt_outdoor(temp, 18, 850 if 10 <= h <= 16 else 300, cfg)
            by_hour.setdefault(f"R{date}T{h:02d}", []).append(
                {"cellId": cid, "hourKey": f"R{date}T{h:02d}", "wbgt": w, "band": core.band_for(w, cfg),
                 "tempC": round(temp, 1), "rh": 18, "swRad": 850, "windMs": 2})
    grid.update_index("replay", grid.store(by_hour, 1), date)
    for i, (lat, lon, kind, name) in enumerate([
        (17.3850, 78.4867, "water", "Water ATM (sample)"), (17.3900, 78.4800, "fuel", "Fuel station (sample)"),
        (17.4000, 78.4900, "shade", "Bus shelter (sample)"), (17.3780, 78.4950, "water", "Temple water point (sample)")]):
        api.POINTS.put_item(Item=api.to_ddb({"pointId": f"sample-{i}", "lat": lat, "lon": lon, "kind": kind,
                                             "name": name, "cellId": core.cell_id(lat, lon)}))


class Handler(BaseHTTPRequestHandler):
    def _api(self, method):
        u = urlparse(self.path)
        length = int(self.headers.get("content-length") or 0)
        event = {"requestContext": {"http": {"method": method}}, "rawPath": u.path[len("/api"):] or "/",
                 "queryStringParameters": dict(parse_qsl(u.query)) or None,
                 "body": self.rfile.read(length).decode() if length else None}
        r = api.handler(event, None)
        body = r["body"].encode()
        self.send_response(r["statusCode"])
        for k, v in r["headers"].items():
            self.send_header(k, v)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _static(self):
        path = urlparse(self.path).path
        path = "/index.html" if path == "/" else path
        if path == "/config.js":
            body = (f'window.HEAT_CONFIG = {{API_URL: "http://localhost:{PORT}/api", LOCATION_API_KEY: "", '
                    f'LOCATION_REGION: "ap-south-1", CENTER: [78.4867, 17.3850]}};').encode()
        else:
            full = os.path.normpath(os.path.join(WEB, path.lstrip("/")))
            if not full.startswith(WEB) or not os.path.isfile(full):
                self.send_error(404)
                return
            with open(full, "rb") as f:
                body = f.read()
        types = {".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".json": "application/json", ".svg": "image/svg+xml"}
        self.send_response(200)
        self.send_header("content-type", types.get(os.path.splitext(path)[1], "application/octet-stream") + "; charset=utf-8")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._api("GET") if self.path.startswith("/api/") else self._static()

    def do_POST(self):
        self._api("POST")

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    seed()
    print(f"HeatShield local: http://localhost:{PORT}/?demo=1&replay=1")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
