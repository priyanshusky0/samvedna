"""Trend tests and seasonal comparison.

Mann-Kendall (Hamed-Rao variance correction for autocorrelation, which weekly
weather and fire series always have) on weekly aggregates, plus Sen's slope.
Only complete weeks (all 7 days observed) enter the test.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pymannkendall as mk

from app.analysis.series import METRICS, SNPP, fires_daily, load, with_total

WEEKLY_AGG = {"count": "sum", "level": "mean"}
TREND_METRICS = ["fires", "rain", "tmax", "news_flood", "news_fire", "news_earthquake", "news_heat_drought"]


def weekly(s: pd.Series, how: str, max_weeks: int = 52) -> pd.Series:
    if s.dropna().empty or not isinstance(s.index, pd.DatetimeIndex):
        return pd.Series(dtype=float)
    s = s[s.index <= pd.Timestamp(date.today())]
    grp = s.resample("W-SUN")
    complete = grp.count() == 7
    agg = grp.sum(min_count=7) if how == "sum" else grp.mean()
    return agg[complete].dropna().tail(max_weeks)


def mann_kendall(w: pd.Series) -> dict | None:
    if len(w) < 8 or w.nunique() < 3:
        return None
    r = mk.hamed_rao_modification_test(w.to_numpy())
    return {
        "trend": r.trend, "p_value": float(r.p), "tau": float(r.Tau),
        "sens_slope": float(r.slope), "n_weeks": len(w),
        "start": str(w.index.min().date()), "end": str(w.index.max().date()),
    }


def run_trends() -> list[dict]:
    rows = []
    for metric in TREND_METRICS:
        m = METRICS[metric]
        wide = with_total(load(metric), metric)
        for region in wide.columns:
            res = mann_kendall(weekly(wide[region].astype(float), WEEKLY_AGG[m.kind]))
            if res:
                unit = m.unit.replace("/day", "/week") if m.kind == "count" else m.unit
                rows.append({"metric": metric, "label": m.label, "region": region,
                             "unit": f"{unit} per week", **res})
    return rows


def run_seasonal(today: date | None = None) -> list[dict]:
    """Month-to-date this year vs the same calendar days last year."""
    today = today or date.today()
    start = today.replace(day=1)
    rows = []
    for metric in ["fires", "rain", "tmax"]:
        m = METRICS[metric]
        # Fires: Suomi-NPP only, the one sensor present in both years.
        raw = fires_daily(satellite=SNPP) if metric == "fires" else load(metric)
        wide = with_total(raw, metric)
        for region in wide.columns:
            s = wide[region]
            if s.dropna().empty:
                continue
            cur = s[(s.index >= pd.Timestamp(start)) & (s.index <= pd.Timestamp(today))].dropna()
            # Same calendar days as were actually observed this year.
            prev = s.reindex(cur.index - pd.DateOffset(years=1)).dropna()
            if cur.empty or prev.empty:
                continue
            f = np.sum if m.kind == "count" or metric == "rain" else np.mean
            c, p = float(f(cur)), float(f(prev))
            rows.append({
                "metric": metric, "label": m.label + (" (Suomi-NPP)" if metric == "fires" else ""),
                "region": region,
                "current_period": f"{cur.index.min():%d %b %Y}–{cur.index.max():%d %b %Y} ({len(cur)} d)",
                "last_year_period": f"{prev.index.min():%d %b %Y}–{prev.index.max():%d %b %Y} ({len(prev)} d)",
                # Normalise counts per observed day so unequal coverage compares fairly.
                "current": c / len(cur) if f is np.sum else c,
                "last_year": p / len(prev) if f is np.sum else p,
                "unit": m.unit,
            })
            r = rows[-1]
            r["change_pct"] = (r["current"] - r["last_year"]) / r["last_year"] * 100 if r["last_year"] else None
    return rows
