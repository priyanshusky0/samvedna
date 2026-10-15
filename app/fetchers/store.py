"""Idempotent bulk upsert that works on both PostgreSQL and SQLite."""
from __future__ import annotations

import math

import pandas as pd
from sqlalchemy import select, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.db import session_scope

CHUNK = 500


def _py(v):
    """pandas/numpy scalars -> plain Python, NaN/NaT -> None (rule R6)."""
    if v is None or v is pd.NaT:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    if isinstance(v, pd.Timestamp):
        return v.to_pydatetime()
    if hasattr(v, "item"):  # numpy scalar
        v = v.item()
        if isinstance(v, float) and math.isnan(v):
            return None
    return v


def upsert(model, df: pd.DataFrame) -> int:
    """Insert-or-update rows of `df` into `model`'s table. Returns # of new rows."""
    if df.empty:
        return 0
    table = model.__table__
    pk = [c.name for c in table.primary_key.columns]
    cols = [c.name for c in table.columns if c.name in df.columns]
    records = [{c: _py(v) for c, v in zip(cols, row)} for row in df[cols].itertuples(index=False)]
    new = 0
    with session_scope() as s:
        dialect = s.bind.dialect.name
        insert = pg_insert if dialect == "postgresql" else sqlite_insert
        for i in range(0, len(records), CHUNK):
            chunk = records[i : i + CHUNK]
            keys = [tuple(r[k] for k in pk) for r in chunk]
            pk_cols = [table.c[k] for k in pk]
            expr = tuple_(*pk_cols).in_(keys) if len(pk) > 1 else pk_cols[0].in_([k[0] for k in keys])
            existing = {tuple(r) for r in s.execute(select(*pk_cols).where(expr))}
            new += sum(1 for k in set(keys) if k not in existing)
            stmt = insert(table).values(chunk)
            update_cols = {c: stmt.excluded[c] for c in cols if c not in pk}
            stmt = (
                stmt.on_conflict_do_update(index_elements=pk, set_=update_cols)
                if update_cols
                else stmt.on_conflict_do_nothing(index_elements=pk)
            )
            s.execute(stmt)
    return new
