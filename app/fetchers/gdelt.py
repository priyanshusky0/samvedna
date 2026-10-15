"""GDELT DOC 2.0 API: article lists plus daily volume / tone timelines.

GDELT is strict about load (<= 1 request per 5 s per IP; the shared client
enforces a 6 s gap) and answers violations with a plain-text message and
HTTP 200, so we detect that text and back off before retrying.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

import pandas as pd
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from app.cleaning.gazetteer import gazetteer
from app.cleaning.normalize import clean_gdelt_articles
from app.fetchers.base import FetchError, FetchRun, fetch_json
from app.fetchers.store import upsert
from app.models import NewsArticle, NewsTimeline

log = logging.getLogger("samvedna.gdelt")
SOURCE = "gdelt"
URL = "https://api.gdeltproject.org/api/v2/doc/doc"

HAZARD_QUERIES: dict[str, str] = {
    "flood": "(flood OR floods OR cyclone OR landslide OR \"heavy rain\") sourcecountry:IN",
    "fire": "(wildfire OR \"forest fire\" OR \"stubble burning\") sourcecountry:IN",
    "earthquake": "(earthquake OR tremor) sourcecountry:IN",
    "heat_drought": "(heatwave OR \"heat wave\" OR drought) sourcecountry:IN",
}

# Headline must contain one of these stems to count as on-topic (rule R9):
# GDELT matches query terms anywhere in the article body, which lets in
# newsletters and unrelated stories that mention "flood" once.
HAZARD_TITLE_TERMS: dict[str, list[str]] = {
    "flood": ["flood", "cyclone", "landslide", "heavy rain", "rainfall", "downpour", "inundat", "waterlog",
              "deluge", "cloudburst", "monsoon", "imd", "red alert", "orange alert", "storm", "depression"],
    "fire": ["fire", "blaze", "burning", "stubble", "smoke"],
    "earthquake": ["earthquake", "quake", "tremor", "seismic", "magnitude"],
    "heat_drought": ["heat", "drought", "temperature", "scorch", "dry spell", "water crisis", "water scarcity"],
}

_vader = SentimentIntensityAnalyzer()


def _gdelt(params: dict, ttl: int | None = None) -> dict:
    """fetch_json with GDELT-specific soft rate-limit handling."""
    for attempt in range(4):
        try:
            return fetch_json(SOURCE, URL, {**params, "format": "json"}, ttl=ttl)
        except FetchError as exc:
            if "limit requests" not in str(exc) or attempt == 3:
                raise
            wait = 20 * (attempt + 1)
            log.warning("GDELT soft rate limit; sleeping %ss", wait)
            time.sleep(wait)
    raise AssertionError("unreachable")


def _ts(d: datetime) -> str:
    return d.strftime("%Y%m%d%H%M%S")


def fetch_articles(hazard: str, start: datetime, end: datetime, ttl: int | None = None) -> list[dict]:
    data = _gdelt(
        {"query": HAZARD_QUERIES[hazard], "mode": "artlist", "maxrecords": 250,
         "sort": "datedesc", "startdatetime": _ts(start), "enddatetime": _ts(end)},
        ttl=ttl,
    )
    return data.get("articles") or []


def fetch_timeline(hazard: str, days: int) -> pd.DataFrame:
    """Daily raw article counts and average tone for one hazard query."""
    span = f"{max(days, 1)}d"
    out = {}
    for mode, col in (("timelinevolraw", "volume"), ("timelinetone", "avg_tone")):
        data = _gdelt({"query": HAZARD_QUERIES[hazard], "mode": mode, "timespan": span,
                       "timelinesmooth": 0})
        series = (data.get("timeline") or [{}])[0].get("data") or []
        s = pd.Series({d["date"]: d.get("value") for d in series}, dtype=float)
        if not s.empty:
            # Timeline dates are UTC days (e.g. 20260716T000000Z); keep the date part.
            s.index = pd.to_datetime(s.index, format="%Y%m%dT%H%M%SZ").date
            s = s.groupby(level=0).sum() if col == "volume" else s.groupby(level=0).mean()
        out[col] = s
    df = pd.DataFrame(out)
    df.index.name = "date"
    df = df.reset_index()
    df["hazard_tag"] = hazard
    return df


def windows(now: datetime, days: int, window_days: int) -> list[tuple[datetime, datetime]]:
    """Newest-first [start, end) windows. Boundaries fall on UTC midnights so
    closed windows have stable URLs and hit the 24 h cache on re-runs."""
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    floor = now - timedelta(days=days)
    out = [(max(midnight, floor), now)] if now > midnight else []
    end = midnight
    while end > floor:
        start = max(end - timedelta(days=window_days), floor)
        out.append((start, end))
        end = start
    return out


def run(days: int = 1, window_days: int = 14, hazards: list[str] | None = None) -> int:
    """Pull articles in `window_days` slices (250 max per request) plus timelines.

    Each window is stored as soon as it arrives and a failed window is skipped
    (and counted in fetch_log), so a GDELT throttling episode only loses that slice.
    """
    now = datetime.now(timezone.utc).replace(microsecond=0)
    gaz = gazetteer()
    with FetchRun(SOURCE) as fr:
        for hazard in hazards or list(HAZARD_QUERIES):
            for w_start, w_end in windows(now, days, window_days):
                # Closed historical windows don't change: cache for a day.
                ttl = 86400 if w_end < now else None
                try:
                    raw = fetch_articles(hazard, w_start, w_end, ttl=ttl)
                except Exception as exc:  # noqa: BLE001
                    log.warning("GDELT %s window %s..%s failed: %s", hazard, w_start, w_end, exc)
                    fr.reject({"window_failed": 1})
                    continue
                fr.fetched += len(raw)
                arts, rej = clean_gdelt_articles(raw, hazard, gaz, HAZARD_TITLE_TERMS[hazard])
                fr.reject(rej)
                if not arts.empty:
                    # Artlist has no per-article tone; score the headline with VADER
                    # (compound, -1..1). Aggregate tone comes from GDELT's timeline.
                    arts["tone"] = arts["title"].map(lambda t: _vader.polarity_scores(t)["compound"])
                fr.inserted += upsert(NewsArticle, arts)
            try:
                fr.inserted += upsert(NewsTimeline, fetch_timeline(hazard, max(days, 7)))
            except Exception as exc:  # noqa: BLE001
                log.warning("GDELT %s timeline failed: %s", hazard, exc)
                fr.reject({"timeline_failed": 1})
        return fr.inserted
