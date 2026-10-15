"""Cleaning rules shared by the fetchers. Each `clean_*` function takes raw
records and returns `(clean DataFrame, Counter of rejected rows by reason)`.

Rules (documented in report/report.md, section 4):
  R1 all timestamps converted to Asia/Kolkata and stored naive-IST
  R2 rows outside the bbox or the selected states are rejected
  R3 range checks: magnitude >= 0, precipitation >= 0, lat/lon valid
  R4 FIRMS: 'low' confidence detections dropped; cross-satellite duplicates
     within 375 m and 30 min collapsed to the highest-confidence detection
  R5 GDELT: duplicate URLs and near-duplicate titles (normalised-title hash)
     collapsed
  R6 missing values stay missing (NaN/None); nothing is silently filled
  R7 every row gets a deterministic primary key so re-runs upsert, not duplicate
  R8 FIRMS archive rows flagged as static land sources / offshore (type 2, 3:
     gas flares, industry) are dropped; only vegetation/other fires kept
  R9 GDELT: articles whose headline mentions none of the hazard's terms are
     dropped as off-topic
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter

import numpy as np
import pandas as pd
from sklearn.neighbors import BallTree

from app.cleaning.geo import tag_states
from app.config import get_settings

IST = "Asia/Kolkata"
EARTH_RADIUS_M = 6_371_000.0
CONF_RANK = {"low": 0, "nominal": 1, "high": 2}
CONF_ALIASES = {"l": "low", "n": "nominal", "h": "high"}


def sha1(*parts: object) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()


def to_ist(ts: pd.Series) -> pd.Series:
    """Parse to UTC-aware datetimes, convert to IST, drop tz info for storage."""
    parsed = pd.to_datetime(ts, utc=True, errors="coerce")
    return parsed.dt.tz_convert(IST).dt.tz_localize(None)


def _reject(df: pd.DataFrame, mask: pd.Series, reason: str, rej: Counter) -> pd.DataFrame:
    n = int(mask.sum())
    if n:
        rej[reason] += n
    return df.loc[~mask]


def in_bbox(lat: pd.Series, lon: pd.Series) -> pd.Series:
    w, s, e, n = get_settings().bbox
    return lat.between(s, n) & lon.between(w, e)


def _geo_filter(df: pd.DataFrame, rej: Counter) -> pd.DataFrame:
    """Shared R2/R3 checks on lat/lon then state tagging."""
    df = df.assign(lat=pd.to_numeric(df["lat"], errors="coerce"),
                   lon=pd.to_numeric(df["lon"], errors="coerce"))
    df = _reject(df, df["lat"].isna() | df["lon"].isna(), "missing_coords", rej)
    df = _reject(df, ~in_bbox(df["lat"], df["lon"]), "outside_bbox", rej)
    df = df.assign(state=tag_states(df["lat"], df["lon"]))
    return _reject(df, df["state"].isna(), "outside_states", rej)


# --------------------------------------------------------------------- FIRMS
def dedup_hotspots(df: pd.DataFrame, distance_m: float, minutes: int) -> tuple[pd.DataFrame, int]:
    """Collapse detections of the same fire by different satellites.

    Greedy: visit detections best-first (confidence, then FRP); each kept point
    suppresses other-satellite points within `distance_m` and `minutes`.
    """
    if df.empty:
        return df, 0
    df = df.assign(_rank=df["confidence"].map(CONF_RANK)).sort_values(
        ["_rank", "frp"], ascending=False
    ).reset_index(drop=True)
    coords = np.radians(df[["lat", "lon"]].to_numpy())
    tree = BallTree(coords, metric="haversine")
    neighbours = tree.query_radius(coords, r=distance_m / EARTH_RADIUS_M)
    t = df["acq_datetime_ist"].to_numpy()
    sat = df["satellite"].to_numpy()
    window = np.timedelta64(minutes, "m")
    dropped = np.zeros(len(df), dtype=bool)
    for i in range(len(df)):
        if dropped[i]:
            continue
        for j in neighbours[i]:
            if j > i and not dropped[j] and sat[j] != sat[i] and abs(t[j] - t[i]) <= window:
                dropped[j] = True
    return df.loc[~dropped].drop(columns="_rank"), int(dropped.sum())


def clean_firms(raw: pd.DataFrame) -> tuple[pd.DataFrame, Counter]:
    rej: Counter = Counter()
    cols = ["lat", "lon", "state", "acq_datetime_ist", "frp", "brightness",
            "confidence", "satellite", "daynight", "id"]
    if raw.empty:
        return pd.DataFrame(columns=cols), rej
    df = raw.rename(columns={"latitude": "lat", "longitude": "lon"}).copy()
    # VIIRS uses bright_ti4; MODIS uses brightness.
    df["brightness"] = pd.to_numeric(
        df["bright_ti4"] if "bright_ti4" in df else df.get("brightness"), errors="coerce"
    )
    df["frp"] = pd.to_numeric(df["frp"], errors="coerce")
    conf = df["confidence"].astype(str).str.strip().str.lower()
    df["confidence"] = conf.map(lambda c: CONF_ALIASES.get(c, c))
    df = _reject(df, ~df["confidence"].isin(CONF_RANK), "bad_confidence", rej)
    df = _reject(df, df["confidence"] == "low", "low_confidence", rej)
    if "type" in df:
        df = _reject(df, pd.to_numeric(df["type"], errors="coerce").isin([2, 3]), "static_or_offshore_source", rej)

    # acq_time is HHMM in UTC, sometimes without leading zeros.
    hhmm = df["acq_time"].astype(str).str.zfill(4)
    df["acq_datetime_ist"] = to_ist(df["acq_date"].astype(str) + " " + hhmm.str[:2] + ":" + hhmm.str[2:])
    df = _reject(df, df["acq_datetime_ist"].isna(), "bad_timestamp", rej)
    df = _geo_filter(df, rej)

    df["id"] = [
        sha1(s, round(a, 5), round(o, 5), d, t)
        for s, a, o, d, t in zip(df["satellite"], df["lat"], df["lon"], df["acq_date"], hhmm)
    ]
    n = len(df)
    df = df.drop_duplicates("id")
    if n - len(df):
        rej["duplicate_id"] += n - len(df)
    cfg = get_settings()
    df, n_dup = dedup_hotspots(df, cfg.firms_dedup_distance_m, cfg.firms_dedup_minutes)
    if n_dup:
        rej["cross_satellite_duplicate"] += n_dup
    return df[cols].reset_index(drop=True), rej


# ---------------------------------------------------------------------- USGS
def clean_usgs(features: list[dict]) -> tuple[pd.DataFrame, Counter]:
    rej: Counter = Counter()
    cols = ["usgs_id", "time_ist", "mag", "depth_km", "place", "lat", "lon", "state", "type"]
    rows = []
    for f in features:
        p, g = f.get("properties") or {}, (f.get("geometry") or {}).get("coordinates") or [None] * 3
        rows.append({
            "usgs_id": f.get("id"), "time_ms": p.get("time"), "mag": p.get("mag"),
            "place": p.get("place"), "type": p.get("type"),
            "lon": g[0], "lat": g[1], "depth_km": g[2] if len(g) > 2 else None,
        })
    if not rows:
        return pd.DataFrame(columns=cols), rej
    df = pd.DataFrame(rows)
    df = _reject(df, df["usgs_id"].isna(), "missing_id", rej)
    df["mag"] = pd.to_numeric(df["mag"], errors="coerce")
    df = _reject(df, df["mag"].isna() | (df["mag"] < 0), "bad_magnitude", rej)
    df["time_ist"] = to_ist(pd.to_datetime(df["time_ms"], unit="ms", utc=True, errors="coerce"))
    df = _reject(df, df["time_ist"].isna(), "bad_timestamp", rej)
    df["depth_km"] = pd.to_numeric(df["depth_km"], errors="coerce")
    df = _geo_filter(df, rej)
    return df.drop_duplicates("usgs_id")[cols].reset_index(drop=True), rej


# ------------------------------------------------------------------- Weather
def clean_weather(state: str, daily: dict, *, is_forecast: bool) -> tuple[pd.DataFrame, Counter]:
    rej: Counter = Counter()
    df = pd.DataFrame({
        "date": pd.to_datetime(daily.get("time", []), errors="coerce").date
        if daily.get("time") else [],
        "precip_mm": daily.get("precipitation_sum"),
        "tmax": daily.get("temperature_2m_max"),
        "tmin": daily.get("temperature_2m_min"),
        "wind_max": daily.get("wind_speed_10m_max"),
    })
    if df.empty:
        return df, rej
    for c in ["precip_mm", "tmax", "tmin", "wind_max"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = _reject(df, df["date"].isna(), "bad_date", rej)
    df = _reject(df, df["precip_mm"] < 0, "negative_precip", rej)
    df = _reject(df, (df["tmax"] < -50) | (df["tmax"] > 60), "tmax_out_of_range", rej)
    # A day with no variables at all (archive lag) is not a real observation.
    df = _reject(df, df[["precip_mm", "tmax", "tmin", "wind_max"]].isna().all(axis=1), "all_missing", rej)
    df["state"] = state
    df["is_forecast"] = is_forecast
    return df.reset_index(drop=True), rej


# --------------------------------------------------------------------- EONET
def clean_eonet(events: list[dict]) -> tuple[pd.DataFrame, Counter]:
    """One row per event geometry (Point) so storm tracks are preserved."""
    rej: Counter = Counter()
    cols = ["eonet_id", "event_id", "title", "category", "date_ist", "lat", "lon", "state", "source_url"]
    rows = []
    for ev in events:
        cat = (ev.get("categories") or [{}])[0].get("title", "Unknown")
        src = (ev.get("sources") or [{}])[0].get("url")
        for g in ev.get("geometry") or []:
            if g.get("type") != "Point":
                # Polygons: use the centroid of the first ring.
                if g.get("type") == "Polygon" and g.get("coordinates"):
                    ring = np.array(g["coordinates"][0], dtype=float)
                    coords = ring.mean(axis=0).tolist()
                else:
                    rej["unsupported_geometry"] += 1
                    continue
            else:
                coords = g.get("coordinates") or [None, None]
            rows.append({
                "event_id": ev.get("id"), "title": ev.get("title"), "category": cat,
                "date": g.get("date"), "lon": coords[0], "lat": coords[1], "source_url": src,
            })
    if not rows:
        return pd.DataFrame(columns=cols), rej
    df = pd.DataFrame(rows)
    df["date_ist"] = to_ist(df["date"])
    df = _reject(df, df["date_ist"].isna(), "bad_timestamp", rej)
    df = _geo_filter(df, rej)
    df["eonet_id"] = [sha1(e, d) for e, d in zip(df["event_id"], df["date_ist"])]
    return df.drop_duplicates("eonet_id")[cols].reset_index(drop=True), rej


# --------------------------------------------------------------------- GDELT
_PUNCT = re.compile(r"[^a-z0-9 ]+")


def normalise_title(title: str) -> str:
    """Lower-case, strip punctuation and whitespace runs: used for near-dup detection."""
    t = _PUNCT.sub(" ", (title or "").lower())
    return " ".join(t.split())


def clean_gdelt_articles(
    articles: list[dict], hazard: str, gazetteer: dict[str, str], title_terms: list[str] | None = None
) -> tuple[pd.DataFrame, Counter]:
    rej: Counter = Counter()
    cols = ["url_hash", "url", "title", "title_hash", "domain", "language", "sourcecountry",
            "seen_at_ist", "tone", "hazard_tag", "state"]
    if not articles:
        return pd.DataFrame(columns=cols), rej
    df = pd.DataFrame(articles)
    df = _reject(df, df.get("url", pd.Series(dtype=str)).isna() | df["title"].isna(), "missing_url_or_title", rej)
    # GDELT seendate format: 20261001T031500Z
    if title_terms:
        lower = df["title"].fillna("").str.lower()
        on_topic = lower.apply(lambda t: any(term in t for term in title_terms))
        df = _reject(df, ~on_topic, "off_topic_headline", rej)
    df["seen_at_ist"] = to_ist(pd.to_datetime(df["seendate"], format="%Y%m%dT%H%M%SZ", utc=True, errors="coerce"))
    df = _reject(df, df["seen_at_ist"].isna(), "bad_timestamp", rej)
    df["url_hash"] = df["url"].map(sha1)
    df["title_hash"] = df["title"].map(lambda t: sha1(normalise_title(t)))
    n = len(df)
    df = df.drop_duplicates("url_hash")
    rej["duplicate_url"] += n - len(df)
    n = len(df)
    df = df.drop_duplicates("title_hash")
    rej["near_duplicate_title"] += n - len(df)
    df["tone"] = pd.to_numeric(df["tone"], errors="coerce") if "tone" in df else np.nan
    df["hazard_tag"] = hazard
    df["state"] = df["title"].map(lambda t: geotag_title(t, gazetteer))
    for c in ["domain", "language", "sourcecountry"]:
        if c not in df:
            df[c] = None
    rej = Counter({k: v for k, v in rej.items() if v})
    return df[cols].reset_index(drop=True), rej


def geotag_title(title: str, gazetteer: dict[str, str]) -> str | None:
    """Return the state whose name / major city appears first in the title."""
    text = f" {normalise_title(title)} "
    best: tuple[int, str] | None = None
    for place, state in gazetteer.items():
        pos = text.find(f" {place} ")
        if pos >= 0 and (best is None or pos < best[0]):
            best = (pos, state)
    return best[1] if best else None
