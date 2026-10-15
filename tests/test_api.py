from datetime import date, datetime, timedelta

import pandas as pd
from fastapi.testclient import TestClient

from app.fetchers.store import upsert
from app.main import app
from app.models import Earthquake, FireHotspot

client = TestClient(app)


def seed():
    now = datetime.now().replace(microsecond=0)
    fires = pd.DataFrame({
        "id": [f"f{i}" for i in range(40)], "lat": 19.9, "lon": 75.3, "state": "Maharashtra",
        "acq_datetime_ist": [now - timedelta(days=i % 20) for i in range(40)], "frp": 3.0,
        "brightness": 330.0, "confidence": "nominal", "satellite": "N", "daynight": "D",
    })
    upsert(FireHotspot, fires)
    upsert(Earthquake, pd.DataFrame([{"usgs_id": "q1", "time_ist": now, "mag": 3.2, "depth_km": 10.0,
                                      "place": "Koyna", "lat": 17.4, "lon": 73.75, "state": "Maharashtra",
                                      "type": "earthquake"}]))


def test_upsert_is_idempotent():
    seed()
    seed()
    r = client.get("/api/summary", params={"days": 30})
    assert r.status_code == 200
    assert r.json()["fires"] == 40 and r.json()["quakes"] == 1


def test_endpoints_respond():
    seed()
    for path, params in [
        ("/api/health", {}), ("/api/meta", {}), ("/api/alerts", {}), ("/api/trends", {}),
        ("/api/correlation", {}), ("/api/seasonal", {}), ("/api/news", {"hazard": "flood"}),
        ("/api/timeseries", {"metric": "fires", "state": "Maharashtra", "days": 30}),
        ("/api/map", {"layer": "fires", "days": 30}), ("/api/map", {"layer": "states"}),
    ]:
        r = client.get(path, params=params)
        assert r.status_code == 200, (path, r.text)


def test_map_geojson_shape():
    seed()
    gj = client.get("/api/map", params={"layer": "quakes", "days": 7, "state": "Maharashtra"}).json()
    assert gj["type"] == "FeatureCollection"
    assert gj["features"][0]["geometry"]["coordinates"] == [73.75, 17.4]


def test_timeseries_has_baseline():
    seed()
    pts = client.get("/api/timeseries", params={"metric": "fires", "state": "Maharashtra", "days": 30}).json()["points"]
    assert pts[-1]["date"] == str(date.today())
    assert any(p["mean"] is not None for p in pts)


def test_bad_inputs_rejected():
    assert client.get("/api/timeseries", params={"metric": "nope"}).status_code == 400
    assert client.get("/api/export", params={"table": "users"}).status_code == 400
    assert client.get("/api/map", params={"layer": "nope"}).status_code == 422


def test_export_csv():
    seed()
    r = client.get("/api/export", params={"table": "earthquakes", "format": "csv", "days": 30})
    assert r.status_code == 200 and r.text.splitlines()[0].startswith("usgs_id")


def test_pages_render():
    assert client.get("/").status_code == 200
    assert client.get("/about").status_code == 200
