# Decisions log

Decisions made during the build, with the reason for each. Newest last.

| # | Decision | Why |
|---|---|---|
| 1 | Python 3.12, FastAPI, SQLAlchemy 2, Alembic, SQLite locally / PostgreSQL in Docker and on Render | Matches the spec. SQLite keeps local runs instant; one Alembic migration works on both (`render_as_batch` for SQLite). |
| 2 | Upserts use dialect-specific `INSERT … ON CONFLICT` on deterministic primary keys (SHA-1 of the natural key) | Re-running any fetch never duplicates rows (verified on Postgres: second run → 0 new rows). |
| 3 | **FIRMS without a key:** fall back to FIRMS' public 7-day South Asia CSVs; add FIRMS' public **yearly India archive** (Suomi-NPP, 2025) for a seasonal baseline | No MAP_KEY was available. The area API (with key) is still the main path and loops over 5-day windows. FIRMS now caps DAY_RANGE at **5**, not 10 (checked in their docs on 2026-10-15). |
| 4 | Fire **year-over-year comparisons use Suomi-NPP only** | The 2025 archive is Suomi-NPP only, but 2026 NRT data has two satellites, so a naive comparison doubles this year's counts (the first run showed a fake +510%). |
| 5 | Extra "same-season" fire alert: today's Suomi-NPP count vs ±15 days a year earlier | Rolling 30-day z-scores need 14+ days of recent history, which the key-less 7-day feed cannot give. Fire activity is also strongly seasonal (crop-residue burning), so last year's same window is a fair baseline. |
| 6 | Earthquakes backfilled for **10 years**, EONET for **1 year** | Peninsular India has only a handful of USGS M2.5+ events per quarter; 90 days alone would be almost empty. |
| 7 | Weather history **5 years** (not 1–2) | STL with a 365-day period cannot separate a one-day spike from the seasonal pattern when each day-of-year has only two values. With 5 cycles, `seasonal_deg=0` and robust weights it can (unit test injects a +5 °C spike and recovers z ≈ 7). |
| 8 | STL is applied to **temperature only**, not rainfall | Rainfall is zero on most days, so the median absolute deviation is ~0 and robust z-scores explode (first run gave z ≈ 10⁹). Rainfall uses the rolling z-score only. |
| 9 | STL threshold 3.5 (not 3) | STL residuals are heavy-tailed (99th percentile \|z\| ≈ 6 on real temperature data), so 3 gave too many alerts. |
| 10 | Minimum absolute values for alerts (fires ≥ 5/day, quakes ≥ 2/day, rain ≥ 15 mm, Tmax ≥ 37 °C, news ≥ 5/day) | A z-score on tiny counts is meaningless (0 → 1 quake gives a huge z). |
| 11 | Rolling baseline excludes the current day (`shift(1)`) | A spike would otherwise inflate its own baseline and hide itself. |
| 12 | Missing days stay `NaN`, never 0. A fire day counts as "covered" if any hotspot exists region-wide that day | Rule R6: "no satellite data" is not the same as "no fires". |
| 13 | Offshore points within 0.25° (~25 km) of a selected state are assigned to the nearest state | Coastal quakes and cyclone centres sit just offshore; strict point-in-polygon would drop them. Points inside an unmonitored Indian state are never snapped. A quake just across the border (e.g. 22 km SE of Jati, Sindh) can still be assigned to Gujarat; this is within USGS location uncertainty. |
| 14 | GDELT: 14-day windows (≤ 250 articles each), sort by date, cache closed windows for 24 h, **adaptive throttle** (each 429 doubles the gap, up to 60 s), failed windows skipped and logged | GDELT throttled hard during development, even with 30–60 s gaps. Skipping a failed window keeps everything else that was fetched. |
| 15 | GDELT headline relevance filter (rule R9): keep only articles whose **title** contains a hazard term | GDELT matches query terms anywhere in the body; the first run returned newsletters and business stories. |
| 16 | Per-article sentiment = VADER compound on the headline; aggregate tone = GDELT `timelinetone` | The artlist API has no per-article tone. |
| 17 | An article matched by two hazard queries keeps the last hazard tag (PK = URL hash, per spec) | Keeps the spec's schema. The overlap is small (flood vs heat queries rarely share headlines). |
| 18 | Trend tests on complete weeks only. Weather (5 years) uses the **Seasonal Mann-Kendall** test (period 52); shorter series use **Hamed-Rao** on the last 52 weeks | A plain test on one year of weather mostly detects the annual cycle (it first reported "Tmax increasing" across one Oct→Oct window). The seasonal test compares like weeks across years. Hamed-Rao corrects for autocorrelation in the short series. |
| 19 | "News volume vs hazard" uses **two stacked charts on a shared time axis**, not a dual-axis chart | Dual y-axes let the chosen scales invent correlations. The statistics tab gives the formal correlation. |
| 20 | Chart series use a colour-blind-validated categorical palette; olive/khaki is only for the interface chrome; status colours (amber/red) are reserved for alert severity and always come with an icon and label | Readability in both themes and for colour-blind viewers. |
| 21 | Map tiles: OpenStreetMap standard tiles with a CSS grey/invert filter for dark mode | CARTO basemaps now require an API key. |
| 22 | Phase commits use the author's own git identity, with no co-author trailers | The author's standing preference. |
| 23 | GitHub repo created **private**; deployment configured with a Render Blueprint (`render.yaml`) | Publishing and connecting Render need the author's own accounts and choice. |
| 24 | Every HTTP request has a 120 s wall-clock deadline (body streamed and timed) | A GDELT request hung for 14 minutes: httpx's read timeout is per chunk, and the server trickled bytes. |
| 25 | GDELT article windows are aligned to UTC midnights | Closed windows then have stable URLs and hit the 24 h cache, so a restarted backfill never re-requests them. |
| 26 | Open-Meteo forecast-API "past days" are mixed with ERA5 archive days | Checked on 20 overlapping days at 3 capitals: mean difference 0.01–0.04 °C, so recent anomalies are not a data-source artefact. |
| 27 | Dashboard state lives in the URL (`?state=&days=&tab=&metric=&hazard=`) | Shareable links, and headless screenshots of each view. |
