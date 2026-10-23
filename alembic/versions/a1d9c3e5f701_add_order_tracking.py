"""add order tracking

Revision ID: a1d9c3e5f701
Revises: b180c66d7cf4
"""
from typing import Sequence, Union
import secrets

import sqlalchemy as sa
from alembic import op

revision: str = 'a1d9c3e5f701'
down_revision: Union[str, Sequence[str], None] = 'b180c66d7cf4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def upgrade() -> None:
    op.add_column("orders", sa.Column("tracking_code", sa.String(12), nullable=True))
    op.add_column("orders", sa.Column("tracking_enabled", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("orders", sa.Column("tracking_stage", sa.String(60), nullable=True))
    op.add_column("orders", sa.Column("due_date", sa.Date(), nullable=True))
    op.add_column("users", sa.Column("tracking_stages", sa.String(2000), nullable=True))
    op.add_column("users", sa.Column("tracking_enabled", sa.Boolean(), nullable=False, server_default=sa.true()))

    bind = op.get_bind()
    ids = [r[0] for r in bind.execute(sa.text("SELECT id FROM orders")).fetchall()]
    used = set()
    for oid in ids:
        while True:
            code = "CL-" + "".join(secrets.choice(_ALPHABET) for _ in range(6))
            if code not in used:
                used.add(code)
                break
        bind.execute(
            sa.text("UPDATE orders SET tracking_code = :c, tracking_enabled = :t WHERE id = :i"),
            {"c": code, "t": False, "i": oid},
        )
    op.create_index("ix_orders_tracking_code", "orders", ["tracking_code"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_orders_tracking_code", table_name="orders")
    with op.batch_alter_table("users") as b:
        b.drop_column("tracking_enabled")
        b.drop_column("tracking_stages")
    with op.batch_alter_table("orders") as b:
        b.drop_column("due_date")
        b.drop_column("tracking_stage")
        b.drop_column("tracking_enabled")
        b.drop_column("tracking_code")
