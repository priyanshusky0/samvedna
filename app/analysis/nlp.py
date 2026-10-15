"""What is the news saying? Keywords, topics and tone per hazard.

- TF-IDF over headlines (unigrams + bigrams). A hazard's keywords are the terms
  with the highest summed TF-IDF weight; weekly keywords do the same per week.
- Topics: NMF on the TF-IDF matrix (k = 5 by default). NMF gives additive,
  non-negative parts, which read more naturally as "themes" than k-means
  centroids on short texts. Each topic reports top terms + example headlines.
- Tone: GDELT's daily average document tone (timelinetone) plus the mean VADER
  compound score of our headlines per week.
"""
from __future__ import annotations

import logging
from datetime import timedelta

import numpy as np
import pandas as pd
from sklearn.decomposition import NMF
from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS, TfidfVectorizer
from sqlalchemy import select

from app.config import get_settings
from app.db import engine
from app.models import NewsArticle, NewsTimeline
from app.timeutil import now_ist

log = logging.getLogger("samvedna.nlp")

# Words that appear in nearly every disaster headline and carry no signal.
EXTRA_STOP = {
    "india", "indian", "news", "says", "said", "amid", "today", "latest", "live", "updates",
    "update", "video", "watch", "check", "day", "new", "year", "years", "people", "know",
    "details", "report", "reports", "hindi", "times", "pm", "cm", "govt", "state", "s", "2026", "2025",
}
STOP = list(ENGLISH_STOP_WORDS | EXTRA_STOP)


def _vectorizer(min_df: int = 2) -> TfidfVectorizer:
    return TfidfVectorizer(stop_words=STOP, ngram_range=(1, 2), min_df=min_df, max_df=0.6,
                           token_pattern=r"(?u)\b[a-zA-Z][a-zA-Z]+\b", sublinear_tf=True)


def load_articles(hazard: str | None = None, state: str | None = None, days: int = 90) -> pd.DataFrame:
    stmt = select(NewsArticle).where(NewsArticle.seen_at_ist >= now_ist() - timedelta(days=days))
    if hazard:
        stmt = stmt.where(NewsArticle.hazard_tag == hazard)
    if state:
        stmt = stmt.where(NewsArticle.state == state)
    with engine.connect() as conn:
        df = pd.read_sql(stmt.order_by(NewsArticle.seen_at_ist.desc()), conn)
    # Keep English headlines: the stop-list and VADER are English-only.
    return df[df["language"].fillna("English").eq("English")]


def top_keywords(titles: pd.Series, n: int = 15) -> list[dict]:
    if len(titles) < 5:
        return []
    try:
        vec = _vectorizer()
        X = vec.fit_transform(titles)
    except ValueError:  # empty vocabulary after min_df
        return []
    weights = np.asarray(X.sum(axis=0)).ravel()
    terms = vec.get_feature_names_out()
    order = weights.argsort()[::-1][:n]
    return [{"term": terms[i], "weight": round(float(weights[i]), 3)} for i in order]


def weekly_keywords(df: pd.DataFrame, n: int = 5) -> list[dict]:
    out = []
    if df.empty:
        return out
    weeks = pd.to_datetime(df["seen_at_ist"]).dt.to_period("W-SUN")
    for week, grp in df.groupby(weeks):
        kws = top_keywords(grp["title"], n)
        if kws:
            out.append({"week_start": str(week.start_time.date()), "n_articles": len(grp),
                        "keywords": [k["term"] for k in kws]})
    return sorted(out, key=lambda w: w["week_start"])


def topics(df: pd.DataFrame, k: int, n_terms: int = 8) -> list[dict]:
    if len(df) < 4 * k:
        return []
    vec = _vectorizer()
    X = vec.fit_transform(df["title"])
    k = min(k, X.shape[1] - 1)
    if k < 2:
        return []
    nmf = NMF(n_components=k, init="nndsvda", random_state=42, max_iter=500)
    W = nmf.fit_transform(X)
    terms = vec.get_feature_names_out()
    assign = W.argmax(axis=1)
    out = []
    for t in range(k):
        idx = np.flatnonzero(assign == t)
        if not len(idx):
            continue
        best = idx[np.argsort(W[idx, t])[::-1][:3]]
        out.append({
            "topic": t + 1,
            "terms": [terms[i] for i in nmf.components_[t].argsort()[::-1][:n_terms]],
            "share": round(len(idx) / len(df), 3),
            "examples": [{"title": df["title"].iloc[i], "url": df["url"].iloc[i]} for i in best],
        })
    return sorted(out, key=lambda t: -t["share"])


def tone_series(hazard: str, df: pd.DataFrame) -> list[dict]:
    with engine.connect() as conn:
        tl = pd.read_sql(select(NewsTimeline.date, NewsTimeline.avg_tone, NewsTimeline.volume)
                         .where(NewsTimeline.hazard_tag == hazard).order_by(NewsTimeline.date), conn)
    vader = (df.assign(day=pd.to_datetime(df["seen_at_ist"]).dt.date).groupby("day")["tone"].mean()
             if not df.empty else pd.Series(dtype=float))
    rows = []
    for r in tl.itertuples():
        v = vader.get(r.date)
        rows.append({"date": str(r.date), "gdelt_tone": None if pd.isna(r.avg_tone) else round(r.avg_tone, 3),
                     "volume": None if pd.isna(r.volume) else r.volume,
                     "headline_sentiment": None if v is None or pd.isna(v) else round(float(v), 3)})
    return rows


def run(hazards: list[str]) -> dict:
    k = get_settings().nlp_topics
    out = {}
    for hz in hazards:
        df = load_articles(hz)
        out[hz] = {
            "n_articles": len(df),
            "keywords": top_keywords(df["title"]),
            "weekly_keywords": weekly_keywords(df),
            "topics": topics(df, k),
            "mean_headline_sentiment": None if df.empty else round(float(df["tone"].mean()), 3),
            "state_counts": df["state"].value_counts().to_dict(),
        }
    return out
