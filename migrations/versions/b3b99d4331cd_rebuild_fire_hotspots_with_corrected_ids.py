"""rebuild fire hotspots with corrected ids

Revision ID: b3b99d4331cd
Revises: f10cc359a0a5
Create Date: 2026-10-15 20:08:50.206780

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b3b99d4331cd'
down_revision: Union[str, None] = 'f10cc359a0a5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Hotspot ids were built from a misaligned time column (fixed in
    # cleaning/normalize.py). Rows are fully re-fetchable, so drop them and the
    # FIRMS fetch log; `backfill --missing-only` on the next boot refetches.
    op.execute("DELETE FROM fire_hotspots")
    op.execute("DELETE FROM fetch_log WHERE source IN ('firms', 'firms_archive')")


def downgrade() -> None:
    pass  # data-only migration; nothing to restore
