"""Pickup location and WhatsApp-only delivery settings."""
from alembic import op
import sqlalchemy as sa
revision = "d3f7a205b903"
down_revision = "c2e6d104f802"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("users", sa.Column("pickup_address", sa.Text(), nullable=True))
    op.add_column("orders", sa.Column("pickup_address", sa.Text(), nullable=True))
    # Existing orders retain their original delivery/payment snapshots.
    op.execute("UPDATE users SET delivery_fee_mode = 'whatsapp', delivery_fee = 0")

def downgrade():
    with op.batch_alter_table("orders") as batch:
        batch.drop_column("pickup_address")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("pickup_address")
