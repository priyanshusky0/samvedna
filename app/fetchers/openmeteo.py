"""Daily weather at one representative point per state.

Primary: Open-Meteo. History from the ERA5-based archive API, recent past and
the next 7 days from the forecast API (archive values win on overlap).
Fallback: NASA POWER (MERRA-2, keyless) when Open-Meteo refuses a state, e.g.
when a shared cloud IP has used up Open-Meteo's free quota. POWER lags ~3 days
and its coarser grid reads cooler at the coast, so rows keep a `source` tag.

Each state is stored as soon as it arrives, so one throttled request never
discards the others, and history is fetched incrementally after the first run.
"""
from __future__ import annotations

import logging
from datetime import timedelta

import pandas as pd
from sqlalchemy import func, select

from app.cleaning.normalize import clean_weather
from app.config import get_settings
from app.db import session_scope
from app.fetchers.base import FetchRun, fetch_json, quota_exhausted
from app.fetchers.store import upsert
from app.models import WeatherDaily
from app.timeutil import today_ist

log = logging.getLogger("samvedna.weather")
SOURCE = "openmeteo"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
POWER_URL = "https://power.larc.nasa.gov/api/temporal/daily/point"
DAILY = "precipitation_sum,temperature_2m_max,temperature_2m_min,wind_speed_10m_max"
POWER_PARAMS = {"PRECTOTCORR": "precipitation_sum", "T2M_MAX": "temperature_2m_max",
                "T2M_MIN": "temperature_2m_min", "WS10M_MAX": "wind_speed_10m_max"}


def _last_observed(state: str):
    with session_scope() as s:
        return s.scalar(select(func.max(WeatherDaily.date))
                        .where(WeatherDaily.state == state, WeatherDaily.is_forecast.is_(False)))


def _open_meteo(state: str, lat: float, lon: float, history_start) -> tuple[pd.DataFrame, int, dict]:
    cfg = get_settings()
    today = today_ist()
    base = {"latitude": lat, "longitude": lon, "daily": DAILY, "timezone": cfg.timezone}
    frames, fetched, rejected = [], 0, {}
    archive_end = today - timedelta(days=6)
    if history_start and history_start <= archive_end:
        data = fetch_json(SOURCE, ARCHIVE_URL, {**base, "start_date": history_start, "end_date": archive_end},
                          ttl=86400)
        df, rej = clean_weather(state, data.get("daily") or {}, is_forecast=False)
        fetched += len((data.get("daily") or {}).get("time", []))
        rejected.update(rej)
        frames.append(df)
    data = fetch_json(SOURCE, FORECAST_URL, {**base, "past_days": 14, "forecast_days": 7})
    df, rej = clean_weather(state, data.get("daily") or {}, is_forecast=False)
    if not df.empty:
        # Days after today are forecasts, not observations.
        df["is_forecast"] = pd.to_datetime(df["date"]) > pd.Timestamp(today)
    fetched += len((data.get("daily") or {}).get("time", []))
    rejected.update(rej)
    frames.append(df)
    frames = [f for f in frames if not f.empty]
    out = pd.concat(frames).drop_duplicates(["state", "date"], keep="first") if frames else pd.DataFrame()
    if not out.empty:
        out["source"] = "open-meteo"
    return out, fetched, rejected


def _nasa_power(state: str, lat: float, lon: float, start) -> tuple[pd.DataFrame, int, dict]:
    data = fetch_json("nasa_power", POWER_URL, {
        "parameters": ",".join(POWER_PARAMS), "community": "AG", "latitude": lat, "longitude": lon,
        "start": start.strftime("%Y%m%d"), "end": today_ist().strftime("%Y%m%d"), "format": "JSON",
    }, ttl=21600)
    params = data.get("properties", {}).get("parameter", {})
    fill = data.get("header", {}).get("fill_value", -999.0)
    days = sorted(next(iter(params.values()), {}).keys())
    daily: dict[str, list] = {"time": [f"{d[:4]}-{d[4:6]}-{d[6:]}" for d in days]}
    for p_name, om_name in POWER_PARAMS.items():
        vals = [params.get(p_name, {}).get(d, fill) for d in days]
        daily[om_name] = [None if v == fill else v for v in vals]
    # POWER wind is m/s; Open-Meteo (and our column) is km/h.
    daily["wind_speed_10m_max"] = [None if v is None else v * 3.6 for v in daily["wind_speed_10m_max"]]
    df, rej = clean_weather(state, daily, is_forecast=False)
    if not df.empty:
        df["source"] = "nasa-power"
    return df, len(days), dict(rej)


def run(history_days: int = 0) -> int:
    """Fetch every state; history is incremental (only days not yet stored)."""
    cfg = get_settings()
    today = today_ist()
    with FetchRun(SOURCE) as fr:
        failed: list[str] = []
        om_blocked = False  # set once Open-Meteo reports an exhausted daily/hourly quota
        for state in cfg.states:
            if state not in cfg.state_points:
                fr.reject({"no_weather_point_configured": 1})
                continue
            lat, lon = cfg.state_points[state]
            last = _last_observed(state)
            want_from = today - timedelta(days=history_days) if history_days else None
            # Re-fetch a week of overlap so late archive corrections land.
            start = max(want_from, last - timedelta(days=7)) if (want_from and last) else want_from
            try:
                if om_blocked:
                    raise RuntimeError("Open-Meteo quota exhausted earlier in this run")
                df, n, rej = _open_meteo(state, lat, lon, start)
            except Exception as exc:  # noqa: BLE001
                om_blocked = om_blocked or quota_exhausted(exc)
                log.warning("Open-Meteo failed for %s (%s); falling back to NASA POWER", state, exc)
                fr.reject({"openmeteo_failed": 1})
                try:
                    df, n, rej = _nasa_power(state, lat, lon, start or (today - timedelta(days=30)))
                    fr.reject({"used_nasa_power_fallback": 1})
                except Exception as exc2:  # noqa: BLE001
                    log.warning("NASA POWER also failed for %s: %s", state, exc2)
                    failed.append(state)
                    continue
            fr.fetched += n
            fr.reject(rej)
            fr.inserted += upsert(WeatherDaily, df)
        if failed and len(failed) == len(cfg.states):
            raise RuntimeError(f"weather failed for every state: {failed}")
        return fr.inserted
