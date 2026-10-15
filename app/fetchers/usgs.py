"""USGS FDSN earthquake catalogue (GeoJSON, no key)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.cleaning.normalize import clean_usgs
from app.config import get_settings
from app.fetchers.base import FetchRun, fetch_json
from app.fetchers.store import upsert
from app.models import Earthquake

SOURCE = "usgs"
URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"


def run(days: int = 2) -> int:
    cfg = get_settings()
    w, s, e, n = cfg.bbox
    end = datetime.now(timezone.utc)
    params = {
        "format": "geojson",
        "starttime": (end - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S"),
        "endtime": end.strftime("%Y-%m-%dT%H:%M:%S"),
        "minlatitude": s, "maxlatitude": n, "minlongitude": w, "maxlongitude": e,
        "minmagnitude": cfg.usgs_min_magnitude,
        "orderby": "time",
    }
    with FetchRun(SOURCE) as fr:
        # Hourly windows make each URL unique, so caching is pointless here.
        data = fetch_json(SOURCE, URL, params, ttl=0)
        features = data.get("features") or []
        fr.fetched = len(features)
        clean, rej = clean_usgs(features)
        fr.reject(rej)
        fr.inserted = upsert(Earthquake, clean)
        return fr.inserted
