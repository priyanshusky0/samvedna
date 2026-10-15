"""Open-Meteo daily weather at one representative point per state.

History comes from the ERA5-based archive API (lags real time by ~5 days);
the forecast API fills the recent past and the next 7 days. Observed archive
values always overwrite earlier forecast values for the same day.
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from app.cleaning.normalize import clean_weather
from app.config import get_settings
from app.fetchers.base import FetchRun, fetch_json
from app.fetchers.store import upsert
from app.models import WeatherDaily

SOURCE = "openmeteo"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
DAILY = "precipitation_sum,temperature_2m_max,temperature_2m_min,wind_speed_10m_max"


def run(history_days: int = 0) -> int:
    """Fetch forecast (+past 14 days) for every state; optionally archive history."""
    cfg = get_settings()
    with FetchRun(SOURCE) as fr:
        frames = []
        for state in cfg.states:
            if state not in cfg.state_points:
                fr.reject({"no_weather_point_configured": 1})
                continue
            lat, lon = cfg.state_points[state]
            base = {"latitude": lat, "longitude": lon, "daily": DAILY, "timezone": cfg.timezone}
            if history_days:
                end = date.today() - timedelta(days=6)
                start = date.today() - timedelta(days=history_days)
                data = fetch_json(SOURCE, ARCHIVE_URL, {**base, "start_date": start, "end_date": end}, ttl=86400)
                df, rej = clean_weather(state, data.get("daily") or {}, is_forecast=False)
                fr.fetched += len((data.get("daily") or {}).get("time", []))
                fr.reject(rej)
                frames.append(df)
            data = fetch_json(SOURCE, FORECAST_URL, {**base, "past_days": 14, "forecast_days": 7})
            df, rej = clean_weather(state, data.get("daily") or {}, is_forecast=False)
            # Days after today are forecasts, not observations.
            df["is_forecast"] = pd.to_datetime(df["date"]) > pd.Timestamp(date.today())
            fr.fetched += len((data.get("daily") or {}).get("time", []))
            fr.reject(rej)
            frames.append(df)
        frames = [f for f in frames if not f.empty]
        if not frames:
            return 0
        # Archive first, forecast-API rows second: keep the archive value on overlap.
        allw = pd.concat(frames).drop_duplicates(["state", "date"], keep="first")
        fr.inserted = upsert(WeatherDaily, allw)
        return fr.inserted
