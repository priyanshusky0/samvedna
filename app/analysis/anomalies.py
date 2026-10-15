"""Anomaly detection.

1. Rolling z-score:  z_t = (x_t - mean_{t-30..t-1}) / std_{t-30..t-1}
   The baseline window excludes day t itself so a spike can't hide inside
   its own baseline. z >= 2 -> medium, z >= 3 -> high.
2. STL residuals: for series with >= 3 years of daily data, STL with a
   365-day seasonal period; residual robust-z = resid / (1.4826 * MAD) >= 3.
   Used for temperature only: rainfall is zero most days, so its MAD is ~0.
3. Same-season baseline (fires): today's Suomi-NPP count vs the mean / std of
   Suomi-NPP daily counts in a +/-15-day window one year earlier. This works
   even when the last 30 days are not available (no FIRMS key) and respects
   the strong seasonality of agricultural / forest burning.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import delete
from statsmodels.tsa.seasonal import STL

from app.analysis.series import METRICS, SNPP, fires_daily, load, with_total
from app.config import get_settings
from app.db import session_scope
from app.models import Alert

log = logging.getLogger("samvedna.anomalies")


def rolling_z(s: pd.Series, window: int, min_periods: int) -> pd.DataFrame:
    base = s.shift(1).rolling(window, min_periods=min_periods)
    mean, std = base.mean(), base.std()
    z = (s - mean) / std.replace(0, np.nan)
    return pd.DataFrame({"value": s, "mean": mean, "std": std, "z": z})


def severity(z: float) -> str | None:
    cfg = get_settings()
    if z >= cfg.z_high:
        return "high"
    if z >= cfg.z_medium:
        return "medium"
    return None


def stl_residual_z(s: pd.Series, period: int = 365) -> pd.DataFrame | None:
    """Columns: expected (trend + seasonal), scale (1.4826*MAD), z."""
    s = s.dropna()
    if len(s) < 3 * period or s.index.to_series().diff().max() > pd.Timedelta(days=1):
        return None  # STL needs several full seasonal cycles of gap-free data
    # Each day-of-year sub-series has only ~5 points (5 years). seasonal_deg=0
    # (local mean) over 13 cycles + robust weights keeps a one-off spike out of
    # the seasonal component; a degree-1 fit through so few points absorbs it.
    res = STL(s, period=period, seasonal=13, seasonal_deg=0, robust=True).fit()
    mad = np.median(np.abs(res.resid - np.median(res.resid)))
    if mad == 0:
        return None
    scale = 1.4826 * mad
    return pd.DataFrame({"expected": res.trend + res.seasonal, "scale": scale, "z": res.resid / scale})


def _fmt(v: float, unit: str) -> str:
    return f"{v:.0f}" if unit.endswith("/day") and "mm" not in unit else f"{v:.1f}"


def detect(metric: str, since: date) -> list[dict]:
    cfg = get_settings()
    m = METRICS[metric]
    wide = with_total(load(metric), metric)
    out: list[dict] = []
    for region in wide.columns:
        s = wide[region].astype(float)
        if s.dropna().empty:
            continue
        rz = rolling_z(s, cfg.anomaly_window_days, cfg.anomaly_min_periods)
        recent = rz[(rz.index >= pd.Timestamp(since)) & rz["z"].notna()]
        for day, r in recent.iterrows():
            sev = severity(r["z"])
            if sev is None or r["value"] < m.min_alert_value:
                continue
            where = "across India" if region == "India" else f"in {region}"
            out.append({
                "date": day.date(), "source": m.source, "method": "zscore", "state": region,
                "metric": metric, "value": r["value"], "baseline_mean": r["mean"],
                "baseline_std": r["std"], "z_score": r["z"], "severity": sev,
                "message": f"{m.label} {where}: {_fmt(r['value'], m.unit)} {m.unit} on {day:%d %b} vs "
                           f"{cfg.anomaly_window_days}-day mean {_fmt(r['mean'], m.unit)} (z = {r['z']:.1f})",
            })
        if m.stl and len(s.dropna()) >= cfg.stl_min_days:
            stl = stl_residual_z(s)
            if stl is None:
                continue
            hits = stl[(stl.index >= pd.Timestamp(since)) & (stl["z"] >= cfg.stl_resid_z)]
            for day, h in hits.iterrows():
                zv = h["z"]
                if s[day] < m.min_alert_value:
                    continue
                out.append({
                    "date": day.date(), "source": m.source, "method": "stl", "state": region,
                    "metric": metric, "value": s[day], "baseline_mean": h["expected"],
                    "baseline_std": h["scale"], "z_score": float(zv), "severity": "high" if zv >= cfg.stl_resid_z + 1 else "medium",
                    "message": f"{m.label} in {region}: {_fmt(s[day], m.unit)} {m.unit} on {day:%d %b} vs "
                               f"{_fmt(h['expected'], m.unit)} expected for the season (STL residual z = {zv:.1f})",
                })
    return out


def seasonal_fire_alerts(since: date, half_window: int = 15) -> list[dict]:
    cfg = get_settings()
    wide = with_total(fires_daily(satellite=SNPP), "fires")
    out = []
    for region in wide.columns:
        s = wide[region]
        for day, value in s[(s.index >= pd.Timestamp(since))].dropna().items():
            ly = day - pd.DateOffset(years=1)
            base = s[(s.index >= ly - pd.Timedelta(days=half_window)) &
                     (s.index <= ly + pd.Timedelta(days=half_window))].dropna()
            if len(base) < half_window or base.std() == 0:
                continue
            z = (value - base.mean()) / base.std()
            sev = severity(z)
            if sev is None or value < METRICS["fires"].min_alert_value:
                continue
            where = f"in {region}" if region != "All states" else "across all states"
            out.append({
                "date": day.date(), "source": "firms", "method": "seasonal", "state": region,
                "metric": "fires", "value": value, "baseline_mean": base.mean(),
                "baseline_std": base.std(), "z_score": z, "severity": sev,
                "message": f"Fire hotspots {where} (Suomi-NPP): {value:.0f} on {day:%d %b} vs "
                           f"{base.mean():.0f}/day at this time last year (z = {z:.1f})",
            })
    return out


def run(history_days: int = 90) -> list[dict]:
    """Recompute alerts for the last `history_days` and replace the alerts table."""
    since = date.today() - timedelta(days=history_days)
    alerts: list[dict] = []
    for metric in METRICS:
        try:
            alerts += detect(metric, since)
        except Exception:
            log.exception("anomaly detection failed for %s", metric)
    try:
        alerts += seasonal_fire_alerts(since)
    except Exception:
        log.exception("seasonal fire alerts failed")
    with session_scope() as s:
        s.execute(delete(Alert))
        s.add_all(Alert(**{k: (float(v) if isinstance(v, (np.floating, np.integer)) else v)
                           for k, v in a.items()}) for a in alerts)
    log.info("alerts written: %d", len(alerts))
    return alerts
