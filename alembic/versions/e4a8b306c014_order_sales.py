"""Link confirmed orders to sales and preserve payment currency."""
from alembic import op
import sqlalchemy as sa
revision = "e4a8b306c014"
down_revision = "d3f7a205b903"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("orders", sa.Column("currency", sa.String(10), nullable=True))
    # Historical order currency was not stored; use the owner's current preference.
    op.execute("UPDATE orders SET currency = UPPER(COALESCE((SELECT preferred_currency FROM users WHERE users.id = orders.user_id), 'NGN'))")
    with op.batch_alter_table("sales") as batch:
        batch.add_column(sa.Column("order_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_sales_order_id_orders", "orders", ["order_id"], ["id"], ondelete="SET NULL")
        batch.create_unique_constraint("uq_sales_order_id", ["order_id"])

def downgrade():
    with op.batch_alter_table("sales") as batch:
        batch.drop_constraint("uq_sales_order_id", type_="unique")
        batch.drop_constraint("fk_sales_order_id_orders", type_="foreignkey")
        batch.drop_column("order_id")
    with op.batch_alter_table("orders") as batch:
        batch.drop_column("currency")
