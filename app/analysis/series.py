"""Turn database tables into tidy daily series: one column per state (or hazard).

Missing coverage stays NaN (cleaning rule R6): a day with no satellite data is
"unknown", not "zero fires". A day is treated as covered for fires if any
hotspot was recorded anywhere in the region that day (with ten large states,
a genuinely fire-free day region-wide does not happen in practice).
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from sqlalchemy import select

from app.config import get_settings
from app.db import engine
from app.models import Earthquake, FireHotspot, NewsArticle, NewsTimeline, WeatherDaily
from app.timeutil import today_ist


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    unit: str
    source: str
    kind: str          # "count" | "level"
    min_alert_value: float  # don't alert below this absolute value
    stl: bool = False  # STL residual check (only for smooth, non-zero-inflated series)


METRICS: dict[str, Metric] = {
    "fires": Metric("fires", "Fire hotspots", "detections/day", "firms", "count", 10),
    "quakes": Metric("quakes", "Earthquakes (M2.5+)", "events/day", "usgs", "count", 2),
    "rain": Metric("rain", "Rainfall", "mm/day", "openmeteo", "level", 15.0),  # zero-inflated: no STL
    "tmax": Metric("tmax", "Max temperature", "°C", "openmeteo", "level", 37.0, stl=True),
    "news_flood": Metric("news_flood", "News: flood/cyclone", "articles/day", "gdelt", "count", 5),
    "news_fire": Metric("news_fire", "News: wildfire", "articles/day", "gdelt", "count", 5),
    "news_earthquake": Metric("news_earthquake", "News: earthquake", "articles/day", "gdelt", "count", 5),
    "news_heat_drought": Metric("news_heat_drought", "News: heat/drought", "articles/day", "gdelt", "count", 5),
}
NEWS_REGION = "India"  # GDELT timelines are national, not per state


def _empty(columns: list[str]) -> pd.DataFrame:
    """An empty frame that still has a DatetimeIndex, so date filters work on it."""
    return pd.DataFrame(columns=columns, dtype=float, index=pd.DatetimeIndex([]))


def _read(stmt) -> pd.DataFrame:
    with engine.connect() as conn:
        return pd.read_sql(stmt, conn)


def _pivot_counts(df: pd.DataFrame, time_col: str, covered: pd.DatetimeIndex | None) -> pd.DataFrame:
    states = get_settings().states
    if df.empty:
        return _empty(states)
    df["day"] = pd.to_datetime(df[time_col]).dt.normalize()
    wide = df.groupby(["day", "state"]).size().unstack("state").reindex(columns=states)
    idx = pd.date_range(wide.index.min(), pd.Timestamp(today_ist()).normalize(), freq="D")
    wide = wide.reindex(idx).fillna(0)
    if covered is not None:
        wide.loc[~wide.index.isin(covered)] = float("nan")
    return wide


# Satellite codes in FIRMS files: "N" = Suomi-NPP, "N20" = NOAA-20.
SNPP = "N"


def fires_daily(satellite: str | None = None) -> pd.DataFrame:
    """Daily hotspot counts. Pass satellite="N" for a single-sensor series that is
    comparable with the Suomi-NPP-only yearly archive."""
    stmt = select(FireHotspot.state, FireHotspot.acq_datetime_ist)
    if satellite:
        stmt = stmt.where(FireHotspot.satellite == satellite)
    df = _read(stmt)
    covered = pd.DatetimeIndex(pd.to_datetime(df["acq_datetime_ist"]).dt.normalize().unique())
    return _pivot_counts(df, "acq_datetime_ist", covered)


def quakes_daily() -> pd.DataFrame:
    # The USGS catalogue is continuous, so every day since the first fetch is covered.
    return _pivot_counts(_read(select(Earthquake.state, Earthquake.time_ist)), "time_ist", None)


def weather_daily(col: str) -> pd.DataFrame:
    df = _read(select(WeatherDaily.state, WeatherDaily.date, getattr(WeatherDaily, col))
               .where(WeatherDaily.is_forecast.is_(False)))
    if df.empty:
        return _empty(get_settings().states)
    wide = df.pivot(index="date", columns="state", values=col)
    wide.index = pd.to_datetime(wide.index)
    return wide.asfreq("D").reindex(columns=get_settings().states)


def news_daily(hazard: str) -> pd.DataFrame:
    df = _read(select(NewsTimeline.date, NewsTimeline.volume).where(NewsTimeline.hazard_tag == hazard))
    if df.empty:
        return _empty([NEWS_REGION])
    s = df.set_index(pd.to_datetime(df["date"]))["volume"].rename(NEWS_REGION)
    return s.asfreq("D").to_frame()


def news_articles_daily(hazard: str) -> pd.DataFrame:
    """Articles per state per day from our own geotagged headlines."""
    df = _read(select(NewsArticle.state, NewsArticle.seen_at_ist)
               .where(NewsArticle.hazard_tag == hazard, NewsArticle.state.is_not(None)))
    return _pivot_counts(df, "seen_at_ist", None)


def load(metric: str) -> pd.DataFrame:
    """Daily wide frame (index = day, columns = state / 'India') for `metric`."""
    if metric == "fires":
        return fires_daily()
    if metric == "quakes":
        return quakes_daily()
    if metric == "rain":
        return weather_daily("precip_mm")
    if metric == "tmax":
        return weather_daily("tmax")
    if metric.startswith("news_"):
        return news_daily(metric.removeprefix("news_"))
    raise KeyError(metric)


def with_total(wide: pd.DataFrame, metric: str) -> pd.DataFrame:
    """Add a region-wide column: sum for counts, mean across states for levels."""
    if NEWS_REGION in wide.columns or wide.empty:
        return wide
    agg = wide.sum(axis=1, min_count=1) if METRICS[metric].kind == "count" else wide.mean(axis=1)
    return wide.assign(**{"All states": agg})
