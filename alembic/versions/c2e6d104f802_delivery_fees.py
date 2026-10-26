"""Add delivery-fee preferences and per-order snapshots."""
from alembic import op
import sqlalchemy as sa
revision = "c2e6d104f802"
down_revision = "a1d9c3e5f701"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("users", sa.Column("delivery_fee_mode", sa.String(20), nullable=False, server_default="upfront"))
    op.add_column("users", sa.Column("delivery_fee", sa.Numeric(12, 2), nullable=False, server_default="0"))
    op.add_column("users", sa.Column("delivery_whatsapp", sa.String(20), nullable=True))
    op.add_column("orders", sa.Column("delivery_fee_mode", sa.String(20), nullable=False, server_default="legacy"))
    op.add_column("orders", sa.Column("delivery_fee", sa.Numeric(12, 2), nullable=True))
    op.add_column("orders", sa.Column("delivery_whatsapp", sa.String(20), nullable=True))

def downgrade():
    with op.batch_alter_table("orders") as batch:
        batch.drop_column("delivery_whatsapp")
        batch.drop_column("delivery_fee")
        batch.drop_column("delivery_fee_mode")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("delivery_whatsapp")
        batch.drop_column("delivery_fee")
        batch.drop_column("delivery_fee_mode")
