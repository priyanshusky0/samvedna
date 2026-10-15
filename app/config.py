"""Application settings, loaded from environment variables / `.env`.

Every tunable used by the pipeline (region, statistical thresholds, schedule)
lives here so it can be justified in one place (see report/report.md).
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent

# Representative point per state for Open-Meteo (state capital).
DEFAULT_STATE_POINTS: dict[str, tuple[float, float]] = {
    "Maharashtra": (19.076, 72.878),      # Mumbai
    "Gujarat": (23.216, 72.637),          # Gandhinagar
    "Goa": (15.490, 73.828),              # Panaji
    "Karnataka": (12.972, 77.594),        # Bengaluru
    "Kerala": (8.524, 76.937),            # Thiruvananthapuram
    "Tamil Nadu": (13.083, 80.271),       # Chennai
    "Andhra Pradesh": (16.506, 80.648),   # Amaravati / Vijayawada
    "Telangana": (17.385, 78.487),        # Hyderabad
    "Rajasthan": (26.912, 75.787),        # Jaipur
    "Madhya Pradesh": (23.260, 77.413),   # Bhopal
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    database_url: str = f"sqlite:///{ROOT / 'samvedna.db'}"
    firms_map_key: str = ""
    enable_scheduler: bool = True
    contact_email: str = ""

    # ---- Area of interest -------------------------------------------------
    states: list[str] = list(DEFAULT_STATE_POINTS)
    # west, south, east, north
    bbox: tuple[float, float, float, float] = (68.0, 6.0, 85.0, 27.0)
    state_points: dict[str, tuple[float, float]] = DEFAULT_STATE_POINTS
    timezone: str = "Asia/Kolkata"

    # ---- Collection -------------------------------------------------------
    backfill_days: int = 90
    weather_history_days: int = 1856  # 5 years + 1 month: enough cycles for STL(period=365)
    firms_sources: list[str] = ["VIIRS_NOAA20_NRT", "VIIRS_SNPP_NRT"]
    firms_window_days: int = 5          # FIRMS area API currently caps DAY_RANGE at 5
    gdelt_min_interval_s: float = 6.0   # GDELT asks for <= 1 req / 5 s; adapts upward on 429
    cache_ttl_s: int = 3600
    usgs_min_magnitude: float = 2.5

    # ---- Cleaning -----------------------------------------------------------
    firms_dedup_distance_m: float = 375.0   # VIIRS I-band pixel size
    firms_dedup_minutes: int = 30

    # ---- Analysis -----------------------------------------------------------
    anomaly_window_days: int = 30
    anomaly_min_periods: int = 14
    z_medium: float = 2.0
    z_high: float = 3.0
    # Ignore "anomalies" on tiny counts (e.g. 0 -> 1 quake gives a huge z).
    min_count_for_alert: int = 5
    stl_min_days: int = 3 * 365  # STL needs several seasonal cycles to separate spikes
    stl_resid_z: float = 3.5  # residuals are heavy-tailed (99th pct |z| ~ 6)
    alert_lookback_days: int = 7         # only alert on recent days
    max_lag_days: int = 3
    nlp_topics: int = 5

    @property
    def raw_dir(self) -> Path:
        return ROOT / "data" / "raw"

    @property
    def cache_dir(self) -> Path:
        return ROOT / "data" / "cache"

    @property
    def geo_path(self) -> Path:
        return ROOT / "data" / "geo" / "india_adm1_simplified.geojson"

    @property
    def user_agent(self) -> str:
        contact = f"; {self.contact_email}" if self.contact_email else ""
        return f"SAMVEDNA/1.0 (student open-data HADR research project{contact})"


@lru_cache
def get_settings() -> Settings:
    return Settings()
