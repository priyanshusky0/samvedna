"""NASA EONET v3 natural events (no key). bbox order is west,north,east,south."""
from __future__ import annotations

from app.cleaning.normalize import clean_eonet
from app.config import get_settings
from app.fetchers.base import FetchRun, fetch_json
from app.fetchers.store import upsert
from app.models import NaturalEvent

SOURCE = "eonet"
URL = "https://eonet.gsfc.nasa.gov/api/v3/events"


def run(days: int = 90) -> int:
    w, s, e, n = get_settings().bbox
    params = {"bbox": f"{w},{n},{e},{s}", "status": "all", "days": days}
    with FetchRun(SOURCE) as fr:
        events = fetch_json(SOURCE, URL, params).get("events") or []
        fr.fetched = len(events)
        clean, rej = clean_eonet(events)
        fr.reject(rej)
        fr.inserted = upsert(NaturalEvent, clean)
        return fr.inserted
