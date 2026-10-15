"""JSON endpoints consumed by the dashboard (and documented at /docs)."""
from __future__ import annotations

import csv
import io
import json
from datetime import date, datetime, timedelta
from typing import Literal

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.analysis import nlp
from app.analysis.anomalies import rolling_z
from app.analysis.series import METRICS, load, with_total
from app.cleaning.geo import states_geojson
from app.config import get_settings
from app.db import get_session
from app.models import (
    Alert, AnalysisResult, Earthquake, FetchLog, FireHotspot, NaturalEvent, NewsArticle,
    NewsTimeline, WeatherDaily,
)

router = APIRouter()
SOURCES = ["firms", "firms_archive", "usgs", "eonet", "openmeteo", "gdelt"]
HAZARDS = ["flood", "fire", "earthquake", "heat_drought"]
MAX_MAP_POINTS = 20_000


def _since(days: int) -> datetime:
    return datetime.combine(date.today() - timedelta(days=days - 1), datetime.min.time())


def _state_filter(stmt, col, state: str | None):
    return stmt.where(col == state) if state else stmt


def _num(v):
    return None if v is None or (isinstance(v, float) and v != v) else round(float(v), 3)


@router.get("/meta", summary="States, metrics and hazards known to the app")
def meta():
    cfg = get_settings()
    return {
        "states": cfg.states,
        "metrics": {k: {"label": m.label, "unit": m.unit, "kind": m.kind} for k, m in METRICS.items()},
        "hazards": HAZARDS,
        "bbox": cfg.bbox,
        "thresholds": {"z_medium": cfg.z_medium, "z_high": cfg.z_high, "window_days": cfg.anomaly_window_days},
    }


@router.get("/summary", summary="KPI cards: counts, active alerts, last update per source")
def summary(state: str | None = None, days: int = Query(30, ge=1, le=730), s: Session = Depends(get_session)):
    since = _since(days)

    def count(model, tcol, scol):
        stmt = select(func.count()).select_from(model).where(tcol >= since)
        return s.scalar(_state_filter(stmt, scol, state)) or 0

    alert_stmt = _state_filter(
        select(Alert.severity, func.count()).where(Alert.date >= date.today() - timedelta(days=7)),
        Alert.state, state,
    ).group_by(Alert.severity)
    active = dict(s.execute(alert_stmt).all())
    fire_days = s.scalar(select(func.count(func.distinct(func.date(FireHotspot.acq_datetime_ist))))
                         .where(FireHotspot.acq_datetime_ist >= since))
    return {
        "state": state, "days": days,
        "fires": count(FireHotspot, FireHotspot.acq_datetime_ist, FireHotspot.state),
        "fire_days_covered": fire_days or 0,
        "quakes": count(Earthquake, Earthquake.time_ist, Earthquake.state),
        "events": s.scalar(_state_filter(select(func.count(func.distinct(NaturalEvent.event_id)))
                                         .where(NaturalEvent.date_ist >= since), NaturalEvent.state, state)) or 0,
        "news": count(NewsArticle, NewsArticle.seen_at_ist, NewsArticle.state),
        "alerts": {"high": active.get("high", 0), "medium": active.get("medium", 0)},
        "last_update": health(s)["sources"],
    }


@router.get("/map", summary="GeoJSON for one map layer")
def map_layer(
    layer: Literal["fires", "quakes", "events", "states"] = "fires",
    days: int = Query(30, ge=1, le=3650),
    state: str | None = None,
    s: Session = Depends(get_session),
):
    if layer == "states":
        return states_geojson()
    since = _since(days)
    if layer == "fires":
        stmt = (select(FireHotspot.lat, FireHotspot.lon, FireHotspot.frp, FireHotspot.acq_datetime_ist,
                       FireHotspot.state, FireHotspot.confidence, FireHotspot.satellite)
                .where(FireHotspot.acq_datetime_ist >= since)
                .order_by(FireHotspot.acq_datetime_ist.desc()).limit(MAX_MAP_POINTS))
        stmt = _state_filter(stmt, FireHotspot.state, state)
        props = lambda r: {"frp": r.frp, "time": str(r.acq_datetime_ist), "state": r.state,  # noqa: E731
                           "confidence": r.confidence, "satellite": r.satellite}
    elif layer == "quakes":
        stmt = select(Earthquake.lat, Earthquake.lon, Earthquake.mag, Earthquake.depth_km, Earthquake.place,
                      Earthquake.time_ist, Earthquake.state, Earthquake.usgs_id).where(
            Earthquake.time_ist >= since, *( [Earthquake.state == state] if state else []))
        props = lambda r: {"mag": r.mag, "depth_km": r.depth_km, "place": r.place,  # noqa: E731
                           "time": str(r.time_ist), "state": r.state,
                           "url": f"https://earthquake.usgs.gov/earthquakes/eventpage/{r.usgs_id}"}
    else:
        stmt = select(NaturalEvent.lat, NaturalEvent.lon, NaturalEvent.title, NaturalEvent.category,
                      NaturalEvent.date_ist, NaturalEvent.state, NaturalEvent.source_url).where(
            NaturalEvent.date_ist >= since, *([NaturalEvent.state == state] if state else []))
        props = lambda r: {"title": r.title, "category": r.category, "time": str(r.date_ist),  # noqa: E731
                           "state": r.state, "url": r.source_url}
    rows = s.execute(stmt).all()
    return {
        "type": "FeatureCollection",
        "truncated": layer == "fires" and len(rows) == MAX_MAP_POINTS,
        "features": [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [r.lon, r.lat]},
                      "properties": props(r)} for r in rows],
    }


@router.get("/timeseries", summary="Daily series with rolling baseline and anomaly flags")
def timeseries(metric: str = "fires", state: str | None = None, days: int = Query(90, ge=7, le=730)):
    if metric not in METRICS:
        raise HTTPException(400, f"unknown metric; choose from {list(METRICS)}")
    cfg = get_settings()
    wide = with_total(load(metric), metric)
    col = state if state in wide.columns else ("India" if "India" in wide.columns else "All states")
    if wide.empty or col not in wide.columns:
        return {"metric": metric, "region": col, "points": []}
    rz = rolling_z(wide[col].astype(float), cfg.anomaly_window_days, cfg.anomaly_min_periods)
    rz = rz[rz.index >= pd.Timestamp(date.today() - timedelta(days=days - 1))]
    rz = rz[rz.index <= pd.Timestamp(date.today())]
    m = METRICS[metric]
    pts = []
    for day, r in rz.iterrows():
        z = r["z"]
        flag = None
        if z == z and r["value"] >= m.min_alert_value:
            flag = "high" if z >= cfg.z_high else "medium" if z >= cfg.z_medium else None
        pts.append({"date": str(day.date()), "value": _num(r["value"]), "mean": _num(r["mean"]),
                    "std": _num(r["std"]), "z": _num(z), "flag": flag})
    return {"metric": metric, "label": m.label, "unit": m.unit, "region": col,
            "national_only": col == "India", "points": pts}


@router.get("/alerts", summary="Anomaly alerts, most severe first")
def alerts(severity: Literal["high", "medium"] | None = None, state: str | None = None,
           days: int = Query(30, ge=1, le=365), limit: int = Query(200, le=1000),
           s: Session = Depends(get_session)):
    stmt = select(Alert).where(Alert.date >= date.today() - timedelta(days=days - 1))
    if severity:
        stmt = stmt.where(Alert.severity == severity)
    if state:
        stmt = stmt.where(Alert.state.in_([state, "All states", "India"]))
    rank = {"high": 0, "medium": 1}
    rows = sorted(s.scalars(stmt), key=lambda a: (rank[a.severity], -a.date.toordinal(), -a.z_score))[:limit]
    return [{"date": str(a.date), "source": a.source, "method": a.method, "state": a.state, "metric": a.metric,
             "value": _num(a.value), "baseline_mean": _num(a.baseline_mean), "z_score": _num(a.z_score),
             "severity": a.severity, "message": a.message} for a in rows]


def _result(s: Session, kind: str):
    row = s.get(AnalysisResult, kind)
    if not row:
        return {"computed_at": None, "results": []}
    return {"computed_at": str(row.computed_at), "results": json.loads(row.payload)}


@router.get("/trends", summary="Mann-Kendall trend tests on weekly series")
def trends(s: Session = Depends(get_session)):
    return _result(s, "trends")


@router.get("/correlation", summary="Spearman correlation of hazards vs news volume, lags 0-3 days")
def correlation(s: Session = Depends(get_session)):
    return _result(s, "correlation")


@router.get("/seasonal", summary="Month-to-date vs the same days last year")
def seasonal(s: Session = Depends(get_session)):
    return _result(s, "seasonal")


HAZARD_METRIC = {"flood": "rain", "fire": "fires", "earthquake": "quakes", "heat_drought": "tmax"}


@router.get("/news", summary="Headlines, keywords, topics and tone for one hazard")
def news(hazard: Literal["flood", "fire", "earthquake", "heat_drought"] = "flood",
         state: str | None = None, days: int = Query(90, ge=1, le=120), s: Session = Depends(get_session)):
    df = nlp.load_articles(hazard, state, days)
    cached = _result(s, "nlp")["results"]
    # Topics are expensive-ish and noisy on small subsets: use the cached
    # national model unless a state filter is set and there is enough text.
    if state:
        topics = nlp.topics(df, get_settings().nlp_topics) if len(df) >= 40 else []
    else:
        topics = (cached.get(hazard) or {}).get("topics", []) if isinstance(cached, dict) else []

    # Dual-axis chart: GDELT daily volume vs the matching physical hazard series.
    metric = HAZARD_METRIC[hazard]
    hz = with_total(load(metric), metric)
    hz_col = state if state in hz.columns else "All states"
    since = pd.Timestamp(date.today() - timedelta(days=days - 1))
    hz_s = hz[hz_col][hz.index >= since] if hz_col in hz.columns else pd.Series(dtype=float)
    hz_map = {str(d.date()): _num(v) for d, v in hz_s.items()}
    tone = [t for t in nlp.tone_series(hazard, df) if t["date"] >= str(since.date())]
    for t in tone:
        t["hazard_value"] = hz_map.get(t["date"])

    latest = df.head(40)
    return {
        "hazard": hazard, "state": state, "n_articles": len(df),
        "hazard_metric": {"key": metric, "label": METRICS[metric].label, "unit": METRICS[metric].unit,
                          "region": hz_col},
        "keywords": nlp.top_keywords(df["title"]) if not df.empty else [],
        "weekly_keywords": nlp.weekly_keywords(df)[-6:] if not df.empty else [],
        "topics": topics,
        "tone_series": tone,
        "mean_headline_sentiment": None if df.empty else _num(df["tone"].mean()),
        "state_counts": df["state"].value_counts().to_dict() if not df.empty else {},
        "articles": [{"title": r.title, "url": r.url, "domain": r.domain, "seen_at": str(r.seen_at_ist),
                      "state": r.state, "sentiment": _num(r.tone)} for r in latest.itertuples()],
    }


@router.get("/health", summary="Last fetch status per data source")
def health(s: Session = Depends(get_session)):
    out = {}
    for src in SOURCES:
        last = s.scalars(select(FetchLog).where(FetchLog.source == src)
                         .order_by(FetchLog.started_at.desc()).limit(1)).first()
        last_ok = s.scalar(select(func.max(FetchLog.finished_at))
                           .where(FetchLog.source == src, FetchLog.status == "ok"))
        out[src] = None if last is None else {
            "status": last.status, "started_at": str(last.started_at), "finished_at": str(last.finished_at),
            "last_success": str(last_ok) if last_ok else None, "fetched": last.fetched,
            "inserted": last.inserted, "rejected": last.rejected,
            "rejected_reasons": json.loads(last.rejected_reasons or "{}"), "error": last.error,
        }
    return {"status": "ok", "sources": out, "firms_key_configured": bool(get_settings().firms_map_key)}


EXPORTABLE = {
    "fire_hotspots": (FireHotspot, FireHotspot.acq_datetime_ist),
    "earthquakes": (Earthquake, Earthquake.time_ist),
    "weather_daily": (WeatherDaily, WeatherDaily.date),
    "natural_events": (NaturalEvent, NaturalEvent.date_ist),
    "news_articles": (NewsArticle, NewsArticle.seen_at_ist),
    "news_timeline": (NewsTimeline, NewsTimeline.date),
    "alerts": (Alert, Alert.date),
    "fetch_log": (FetchLog, FetchLog.started_at),
}


@router.get("/export", summary="Download a table as CSV")
def export(table: str, format: Literal["csv"] = "csv", days: int = Query(90, ge=1, le=3650),
           s: Session = Depends(get_session)):
    if table not in EXPORTABLE:
        raise HTTPException(400, f"table must be one of {sorted(EXPORTABLE)}")
    model, tcol = EXPORTABLE[table]
    cols = [c.name for c in model.__table__.columns]
    since = _since(days)
    since_val = since.date() if table in ("weather_daily", "news_timeline", "alerts") else since
    rows = s.execute(select(*[model.__table__.c[c] for c in cols]).where(tcol >= since_val).order_by(tcol)).all()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    w.writerows(rows)
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{table}_{days}d.csv"'})
