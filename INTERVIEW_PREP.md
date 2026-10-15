# Interview prep: SAMVEDNA

A plain-language walk through every module, then 15 likely questions with model answers.

---

## 1. The 30-second pitch

> "SAMVEDNA pulls five open data sources: satellite fire detections, earthquakes, weather, NASA's natural-event feed and Indian news from GDELT. It covers ten states of southern and western India. Every record is cleaned, tagged to a state with point-in-polygon, and stored in PostgreSQL. Simple, explainable statistics flag what's unusual today: rolling z-scores, STL seasonal residuals, and a same-season baseline for fires. A FastAPI back end serves a Leaflet and Chart.js dashboard with alerts, trends, news topics and data-health monitoring. It's dockerised, tested, and documented in a technical report."

---

## 2. Module walk-through

### `app/config.py`: one place for every knob
A `pydantic-settings` class reads `.env` and environment variables. It holds:
- the region: state list, bounding box, one weather point per state;
- collection settings: backfill days, FIRMS window, GDELT gap, cache TTL;
- cleaning thresholds: the 375 m / 30 min fire de-dup;
- analysis thresholds: the 30-day window, z = 2 / 3, minimum alert values, STL settings.

Keeping them all here means every statistical choice can be justified in one place and changed without touching code.

### `app/db.py` and `app/models.py`: storage
`db.py` builds one SQLAlchemy engine from `DATABASE_URL`. It fixes Render's `postgres://` scheme, uses `psycopg` for Postgres and SQLite for dev. It provides `session_scope()`, which commits on success and rolls back on error, and a FastAPI dependency. `models.py` defines nine tables:
- `fire_hotspots`, `earthquakes`, `weather_daily`, `natural_events`
- `news_articles`, `news_timeline`
- `alerts`, `fetch_log`
- `analysis_results`, a JSON cache for the trend, correlation and NLP outputs.

Times are stored as naive IST, and the tables have indexes on time, state and source. **Alembic** manages the schema (`migrations/`).

### `app/fetchers/base.py`: polite HTTP
Every request goes through one `httpx` client with a project User-Agent. Around that client:
- **Throttle:** a per-host minimum gap (6 s for GDELT) that doubles after every HTTP 429.
- **Retry:** `tenacity` retries with exponential backoff on network errors, 429 and 5xx.
- **Cache:** an on-disk cache (1 h by default).
- **Raw archive:** each response is copied under `data/raw/<source>/<date>/` with an index file.
- **`fetch_json`:** detects non-JSON bodies (GDELT sends plain-text errors with HTTP 200) and raises a clear error.
- **`FetchRun`:** a context manager that writes one `fetch_log` row (fetched, inserted, rejected by reason, status, error) whether the fetch succeeds or fails.

### `app/fetchers/store.py`: idempotent writes
`upsert(model, df)` converts NaN to `None` and runs `INSERT … ON CONFLICT DO UPDATE` in chunks of 500, using the Postgres or SQLite dialect as needed. It counts genuinely new keys first, so `fetch_log.inserted` is honest. A second run inserts 0 rows.

### The five fetchers
- **`firms.py`**
  - With a MAP_KEY: FIRMS area API, looping over 5-day windows for both VIIRS satellites.
  - Without one: the public 7-day South Asia CSVs.
  - `run_archive(year)` streams the public yearly India file to disk once and processes it in chunks.
- **`usgs.py`:** FDSN GeoJSON query by bbox and minimum magnitude.
- **`openmeteo.py`:** ERA5 archive (5 years) plus forecast API (past 14 days and next 7) at each state capital. Observed days win over forecast days, and forecast days are flagged and excluded from analysis.
- **`eonet.py`:** v3 events by bbox (note EONET's west, north, east, south order). Every geometry point becomes a row, so storm tracks survive.
- **`gdelt.py`:**
  - Four hazard queries with `sourcecountry:IN`.
  - Articles in 14-day windows (250 max each), plus daily `timelinevolraw` and `timelinetone`.
  - Each window is saved immediately, and a failed window is skipped and counted.
  - Headline sentiment comes from VADER.

### `app/cleaning/`
- **`normalize.py`:** rules R1–R9, one `clean_*` function per source. Each returns `(clean DataFrame, Counter of rejections)`. Highlights:
  - IST conversion.
  - bbox and state filters.
  - FIRMS low-confidence drop.
  - Cross-satellite de-dup: a BallTree with haversine distance, greedy best-confidence-first, 375 m (the VIIRS pixel) and 30 min.
  - Static industrial sources dropped.
  - GDELT URL and normalised-title de-dup, plus the off-topic headline filter.
- **`geo.py`:**
  - Loads the geoBoundaries ADM1 GeoJSON and strips diacritics (*Mahārāshtra → Maharashtra*).
  - Builds a shapely `STRtree` for vectorised point-in-polygon.
  - Points up to ~25 km offshore snap to the nearest monitored state, but never when they sit inside an unmonitored Indian state.
- **`gazetteer.py`:** a hand-made list of state names and major cities for tagging headlines to a state.

### `app/analysis/`
- **`series.py`:** turns tables into daily wide frames (rows = days, columns = states). Gaps stay NaN. Metric metadata (label, unit, count/level, minimum alert value, whether STL applies) lives here.
- **`anomalies.py`:** three detectors write to `alerts`, each with a readable message.
  - Rolling z-score with a baseline that excludes today.
  - STL residual robust-z for temperature.
  - Same-season baseline for fires.
- **`trends.py`:** Mann-Kendall (Hamed-Rao) and Sen's slope on complete weeks; month-to-date vs the same days last year.
- **`correlation.py`:** lagged Spearman (lags 0–3) between hazards and news volume, nationally and per state.
- **`nlp.py`:** TF-IDF keywords overall and weekly, NMF topics with example headlines, and tone series.

### `app/api/routes.py`, `app/main.py`, `app/scheduler.py`
- **`routes.py`:** the JSON endpoints, with validated query parameters (`Literal`, `Query(ge=…, le=…)`), returning GeoJSON for the map. CSV export only allows whitelisted tables.
- **`main.py`:** mounts the API, the static files and two Jinja pages, and starts APScheduler in the FastAPI lifespan.
- **`scheduler.py`:** FIRMS, USGS and EONET hourly; GDELT every 2 h; weather daily. Each job runs the analysis afterwards, guarded by a lock so two runs never overlap.

### Front end (`templates/`, `static/`)
Server-rendered HTML plus about 400 lines of vanilla JS. One state object (`{state, days}`) drives every panel. The page has:
- Leaflet: fire heatmap weighted by fire radiative power, quake circles sized by magnitude, clustered EONET markers, and clickable state polygons.
- Chart.js: a line with a ±2σ band and anomaly points, plus stacked aligned charts for coverage vs hazard.
- Loading skeletons and empty states.
- A dark/light theme stored in `localStorage`.
- All headline text escaped before it is inserted into the page (XSS safety).

### Ops
- `Dockerfile` (python:3.12-slim).
- `docker-compose.yml` (app + postgres:16 with a healthcheck).
- `scripts/entrypoint.sh`: migrate, then a background backfill if the database is empty, then serve.
- `render.yaml`: a Render Blueprint.
- Tests: 32 pytest tests covering cleaning rules, statistics, API routes and an empty database.

---

## 3. Fifteen likely questions

**1. Why a rolling z-score instead of a fixed threshold like "more than 100 fires"?**
A fixed threshold ignores context. 100 fires is normal for Madhya Pradesh in April but extreme for Kerala in July. A z-score measures how unusual today is *for that state and that time*. The baseline excludes today (`shift(1)`), so a spike can't inflate its own baseline. Thresholds of 2 and 3 roughly mean "a 1-in-40 day" and "a 1-in-700 day" under normality. The data isn't normal, so I treat z as a ranking score and add a minimum absolute value to suppress noise.

**2. Your data isn't normal: counts are skewed and rainfall is mostly zero. Isn't the z-score wrong?**
Partly, yes. That's why:
- (a) count metrics (fires, quakes, news) use z on log(1 + count), which cut fire alerts from 102 to 22 in 90 days, and every alert also needs a minimum absolute value;
- (b) rainfall does not use STL, because its median absolute deviation is ~0 and the first run produced z ≈ 10⁹;
- (c) STL uses a robust scale (1.4826 × MAD);
- (d) the trend and correlation tests are rank-based (Mann-Kendall, Spearman).

A next step would be a Poisson or negative-binomial model for counts, flagging tail probabilities.

**3. How does STL work and why did you need 5 years of weather?**
STL splits a series into trend, seasonal and residual parts using LOESS smoothing. The seasonal part is estimated by smoothing each day-of-year across years. With two years, each day-of-year has only two values, so the seasonal fit passed straight through them and absorbed real spikes (my unit test caught this). With five years, `seasonal_deg=0` and robust weights, an injected +5 °C spike comes back at z ≈ 7.

**4. How do you avoid counting the same fire twice?**
NOAA-20 and Suomi-NPP can both detect the same fire. I collapse detections from *different* satellites within 375 m (the VIIRS I-band pixel size) and 30 minutes. A BallTree with the haversine metric finds the neighbours, and the code keeps the highest-confidence detection (then the highest FRP) first. Detections from the same satellite are never merged: two nearby pixels are genuinely two hot pixels.

**5. Why did you compare fires using only Suomi-NPP?**
The 2025 baseline comes from FIRMS' yearly archive, which is Suomi-NPP only, while this year's feed has two satellites. A naive comparison doubled this year and showed a fake +510%. Restricting both years to the same sensor is the like-for-like comparison. It's a good example of checking that "more data" isn't really "different data".

**6. How is the pipeline idempotent?**
Every row gets a deterministic primary key:
- the USGS id or EONET id;
- SHA-1 of satellite, rounded lat/lon, date and time for FIRMS;
- SHA-1 of the URL for news;
- (state, date) for weather.

Writes are `INSERT … ON CONFLICT DO UPDATE`. Re-running a backfill updates rows in place. I verified this on Postgres: the second run inserted 0 rows.

**7. How do you respect API terms and rate limits?**
- Only public, non-personal data, with attribution.
- A descriptive User-Agent on every request.
- A per-host minimum gap (GDELT ≥ 6 s, as its docs ask) that doubles after each 429.
- Exponential backoff.
- A 1-hour cache, and 24 hours for closed historical windows.
- Archived raw responses, so nothing has to be re-downloaded for an audit.

In practice GDELT still throttled heavily, so the fetcher saves each window as it arrives and skips failed ones rather than retrying aggressively.

**8. How do you tag a point to a state? What about the coast?**
A shapely `STRtree` over the geoBoundaries state polygons gives a vectorised point-in-polygon. Points just offshore, like coastal quakes or cyclone centres, snap to the nearest monitored state if within ~25 km, but not when they fall inside an Indian state I don't monitor. News uses a different method: a gazetteer of state and city names matched as whole words in the headline.

**9. What does the Mann-Kendall test tell you that a regression slope doesn't?**
It's non-parametric. It counts whether later values tend to be bigger than earlier ones, so it doesn't assume linearity or normal errors and it's robust to outliers. Sen's slope (the median of pairwise slopes) gives the rate. I use the Hamed-Rao version because weekly weather is autocorrelated, and the original test would report false significance.

**10. Why Spearman with lags for news vs hazards?**
Hazard counts and news volumes are skewed and only monotonically related, which suits a rank correlation. Lags show *timing*: if the correlation peaks at lag 1, coverage follows the physical signal by about a day. That matters if news is used as an early or confirming signal. It is correlation, not causation, and the national news series mixes in events outside my ten states.

**11. Why NMF for topics rather than LDA or k-means?**
Headlines are very short. NMF on TF-IDF gives non-negative, additive "parts" that read as clean themes, it's deterministic with a fixed seed, and it's fast. LDA needs longer documents to estimate word distributions well. K-means forces each headline into exactly one spherical cluster. I still report each topic's share and example headlines so a human can judge it.

**12. How do you handle missing data?**
Missing values are never silently filled. A day with no satellite coverage is NaN, not zero fires. Weather days with all variables missing are rejected. Charts show gaps as breaks, and the UI says how many days are missing. Weekly trend tests only use complete weeks.

**13. How would you scale this to all of India, or to hourly updates?**
- Move the scheduler out of the web process (Celery/RQ or a cron worker) so web replicas don't duplicate jobs.
- Partition the hotspot table by month, and add PostGIS with spatial indexes.
- Precompute daily aggregates into a materialised table instead of aggregating in pandas per request.
- Put the API behind a cache.
- For news, use GDELT's raw event or GKG files on BigQuery instead of the throttled DOC API.

**14. What would you change about the security of the app?**
- Endpoints are read-only and parameters are validated.
- CSV export is whitelisted.
- SQL is parameterised through SQLAlchemy.
- Headline text is escaped in the front end.
- Secrets live in `.env` and are redacted from logs (the FIRMS key is masked in archived URLs).

For production I'd add rate limiting on the API, a CSP header, pinned dependency hashes, and authentication for the export endpoint.

**15. What were the hardest problems and what did you learn?**
- GDELT's aggressive throttling: I moved to adaptive backoff and per-window saving.
- Discovering the satellite mismatch in year-over-year fire counts.
- STL silently absorbing anomalies with too few seasonal cycles.
- A subtle id bug: fire ids were zipped against an unfiltered column, so they depended on which *other* rows were dropped. It only showed up as 209k duplicates when I changed the filter order. I fixed it, added a regression test, and wrote a data migration to rebuild the table.
- Deployment surprises: UTC server clocks and a shared cloud IP that hit Open-Meteo's quota. I fixed them with explicit IST clocks and a NASA POWER fallback.

All of these were caught because I checked outputs against intuition and wrote tests for the edge cases. The lesson: statistics code can run without errors and still be wrong, so sanity checks and documented assumptions are part of the job.
