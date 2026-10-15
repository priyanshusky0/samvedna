"""NASA FIRMS active-fire hotspots (VIIRS NOAA-20 + Suomi-NPP).

With `FIRMS_MAP_KEY` set we use the area API (DAY_RANGE capped at 5 by FIRMS,
so long backfills loop over 5-day windows). Without a key we fall back to the
public, key-less 7-day South Asia CSVs FIRMS publishes for download.

`run_archive(year)` loads FIRMS' public yearly per-country file (Suomi-NPP,
standard-processing) to give a full-year seasonal baseline without a key.
"""
from __future__ import annotations

import io
import logging
from datetime import date, timedelta

import pandas as pd

from app.cleaning.normalize import clean_firms
from app.config import get_settings
from app.fetchers.base import FetchError, FetchRun, _client, fetch_text
from app.fetchers.store import upsert
from app.models import FireHotspot

log = logging.getLogger("samvedna.firms")
SOURCE = "firms"
AREA_URL = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"
OPEN_CSV = {
    "VIIRS_NOAA20_NRT": "https://firms.modaps.eosdis.nasa.gov/data/active_fire/noaa-20-viirs-c2/csv/J1_VIIRS_C2_South_Asia_7d.csv",
    "VIIRS_SNPP_NRT": "https://firms.modaps.eosdis.nasa.gov/data/active_fire/suomi-npp-viirs-c2/csv/SUOMI_VIIRS_C2_South_Asia_7d.csv",
}


def _parse_csv(body: str) -> pd.DataFrame:
    body = body.strip()
    if not body or not body.startswith("latitude"):
        # FIRMS returns plain-text errors such as "Invalid MAP_KEY."
        if body:
            raise FetchError(f"FIRMS error: {body[:200]}")
        return pd.DataFrame()
    return pd.read_csv(io.StringIO(body))


def _windows(start: date, end: date, size: int):
    d = start
    while d <= end:
        yield d, min(size, (end - d).days + 1)
        d += timedelta(days=size)


def fetch_raw(days: int) -> pd.DataFrame:
    cfg = get_settings()
    frames = []
    if cfg.firms_map_key:
        w, s, e, n = cfg.bbox
        area = f"{w},{s},{e},{n}"
        end = date.today()
        start = end - timedelta(days=days - 1)
        for source in cfg.firms_sources:
            for d, rng in _windows(start, end, cfg.firms_window_days):
                url = f"{AREA_URL}/{cfg.firms_map_key}/{source}/{area}/{rng}/{d.isoformat()}"
                # Past windows never change, so cache them for a day.
                ttl = 86400 if d + timedelta(days=rng) < end else None
                frames.append(_parse_csv(fetch_text(SOURCE, url, ttl=ttl, redact=cfg.firms_map_key)))
    else:
        if days > 7:
            log.warning("No FIRMS_MAP_KEY: only the last 7 days are available from the open CSVs")
        for source in cfg.firms_sources:
            frames.append(_parse_csv(fetch_text(SOURCE, OPEN_CSV[source])))
    frames = [f for f in frames if not f.empty]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


ARCHIVE_URL = "https://firms.modaps.eosdis.nasa.gov/data/country/viirs-snpp/{year}/viirs-snpp_{year}_India.csv"


def run_archive(year: int) -> int:
    """Stream the yearly India file to data/raw (once), clean and upsert it."""
    cfg = get_settings()
    dest = cfg.raw_dir / "firms_archive" / f"viirs-snpp_{year}_India.csv"
    with FetchRun(f"{SOURCE}_archive") as fr:
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_suffix(".part")
            with _client.stream("GET", ARCHIVE_URL.format(year=year)) as resp:
                resp.raise_for_status()
                with tmp.open("wb") as f:
                    for chunk in resp.iter_bytes():
                        f.write(chunk)
            tmp.rename(dest)
        for raw in pd.read_csv(dest, chunksize=100_000):
            fr.fetched += len(raw)
            clean, rej = clean_firms(raw)
            fr.reject(rej)
            fr.inserted += upsert(FireHotspot, clean)
        return fr.inserted


def run(days: int = 2) -> int:
    with FetchRun(SOURCE) as fr:
        raw = fetch_raw(days)
        fr.fetched = len(raw)
        clean, rej = clean_firms(raw)
        fr.reject(rej)
        fr.inserted = upsert(FireHotspot, clean)
        return fr.inserted
