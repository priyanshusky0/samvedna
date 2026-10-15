"""SQLAlchemy ORM models. All datetimes are stored as naive Asia/Kolkata (IST)."""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class FireHotspot(Base):
    __tablename__ = "fire_hotspots"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    state: Mapped[str] = mapped_column(String(64), index=True)
    acq_datetime_ist: Mapped[datetime] = mapped_column(DateTime, index=True)
    frp: Mapped[float | None] = mapped_column(Float)
    brightness: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[str | None] = mapped_column(String(16))
    satellite: Mapped[str | None] = mapped_column(String(16), index=True)
    daynight: Mapped[str | None] = mapped_column(String(1))
    ingested_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Earthquake(Base):
    __tablename__ = "earthquakes"
    usgs_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    time_ist: Mapped[datetime] = mapped_column(DateTime, index=True)
    mag: Mapped[float] = mapped_column(Float)
    depth_km: Mapped[float | None] = mapped_column(Float)
    place: Mapped[str | None] = mapped_column(String(255))
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    state: Mapped[str] = mapped_column(String(64), index=True)
    type: Mapped[str | None] = mapped_column(String(32))


class WeatherDaily(Base):
    __tablename__ = "weather_daily"
    state: Mapped[str] = mapped_column(String(64), primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    precip_mm: Mapped[float | None] = mapped_column(Float)
    tmax: Mapped[float | None] = mapped_column(Float)
    tmin: Mapped[float | None] = mapped_column(Float)
    wind_max: Mapped[float | None] = mapped_column(Float)
    is_forecast: Mapped[bool] = mapped_column(default=False)


class NaturalEvent(Base):
    __tablename__ = "natural_events"
    # One row per event geometry point, so a moving storm keeps its track.
    eonet_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(255))
    category: Mapped[str] = mapped_column(String(64), index=True)
    date_ist: Mapped[datetime] = mapped_column(DateTime, index=True)
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    state: Mapped[str] = mapped_column(String(64), index=True)
    source_url: Mapped[str | None] = mapped_column(Text)


class NewsArticle(Base):
    __tablename__ = "news_articles"
    url_hash: Mapped[str] = mapped_column(String(40), primary_key=True)
    url: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    title_hash: Mapped[str] = mapped_column(String(40), index=True)
    domain: Mapped[str | None] = mapped_column(String(255))
    language: Mapped[str | None] = mapped_column(String(32))
    sourcecountry: Mapped[str | None] = mapped_column(String(64))
    seen_at_ist: Mapped[datetime] = mapped_column(DateTime, index=True)
    tone: Mapped[float | None] = mapped_column(Float)
    hazard_tag: Mapped[str] = mapped_column(String(32), index=True)
    state: Mapped[str | None] = mapped_column(String(64), index=True)


class NewsTimeline(Base):
    __tablename__ = "news_timeline"
    hazard_tag: Mapped[str] = mapped_column(String(32), primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    volume: Mapped[float | None] = mapped_column(Float)
    avg_tone: Mapped[float | None] = mapped_column(Float)


class Alert(Base):
    __tablename__ = "alerts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    date: Mapped[date] = mapped_column(Date, index=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    method: Mapped[str] = mapped_column(String(16))
    state: Mapped[str] = mapped_column(String(64), index=True)
    metric: Mapped[str] = mapped_column(String(64))
    value: Mapped[float] = mapped_column(Float)
    baseline_mean: Mapped[float] = mapped_column(Float)
    baseline_std: Mapped[float] = mapped_column(Float)
    z_score: Mapped[float] = mapped_column(Float)
    severity: Mapped[str] = mapped_column(String(8), index=True)
    message: Mapped[str] = mapped_column(Text)


class FetchLog(Base):
    __tablename__ = "fetch_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    fetched: Mapped[int] = mapped_column(Integer, default=0)
    inserted: Mapped[int] = mapped_column(Integer, default=0)
    rejected: Mapped[int] = mapped_column(Integer, default=0)
    # JSON object of reason -> count, e.g. {"low_confidence": 12, "outside_aoi": 300}
    rejected_reasons: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16))
    error: Mapped[str | None] = mapped_column(Text)


class AnalysisResult(Base):
    """Cached JSON outputs of trend / correlation / NLP runs (one row per kind)."""
    __tablename__ = "analysis_results"
    kind: Mapped[str] = mapped_column(String(32), primary_key=True)
    computed_at: Mapped[datetime] = mapped_column(DateTime)
    payload: Mapped[str] = mapped_column(Text)


Index("ix_fire_state_time", FireHotspot.state, FireHotspot.acq_datetime_ist)
Index("ix_news_hazard_time", NewsArticle.hazard_tag, NewsArticle.seen_at_ist)
