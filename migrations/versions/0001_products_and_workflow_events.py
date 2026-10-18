"""Initial product and append-only workflow event schema."""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "products",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("sku", sa.Text(), nullable=True),
        sa.Column("barcode", sa.Text(), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("normalized_name", sa.Text(), nullable=False),
        sa.Column("brand", sa.Text(), nullable=True),
        sa.Column("category", sa.Text(), nullable=True),
        sa.Column("variant", sa.Text(), nullable=True),
        # Decimal strings are lossless on both SQLite and PostgreSQL.
        sa.Column("size_value", sa.Text(), nullable=True),
        sa.Column("size_unit", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("revision >= 1", name="ck_products_revision"),
        sa.CheckConstraint("length(trim(name)) > 0", name="ck_products_name"),
        sa.CheckConstraint("length(trim(normalized_name)) > 0", name="ck_products_normalized_name"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_products_barcode", "products", ["barcode"])
    op.create_index("ix_products_sku", "products", ["sku"])
    op.create_table(
        "workflow_events",
        sa.Column(
            "sequence",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            sa.Identity(),
            nullable=False,
        ),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("correlation_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=True),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.CheckConstraint("schema_version >= 1", name="ck_workflow_events_schema_version"),
        sa.CheckConstraint(
            "event_type IN ('INVENTORY_IMPORTED', 'PRODUCT_EXTRACTED', 'DUPLICATE_CHECKED', "
            "'DUPLICATE_FOUND', 'PRODUCT_APPROVED', 'PRODUCT_CREATED', 'PRODUCT_UPDATED', "
            "'PRODUCT_REJECTED')",
            name="ck_workflow_events_type",
        ),
        sa.PrimaryKeyConstraint("sequence"),
        sa.UniqueConstraint("event_id"),
        sqlite_autoincrement=True,
    )
    op.create_index(
        "ix_workflow_events_type_occurred_at", "workflow_events", ["event_type", "occurred_at"]
    )
    op.create_index("ix_workflow_events_product_id", "workflow_events", ["product_id"])
    op.create_index("ix_workflow_events_correlation_id", "workflow_events", ["correlation_id"])


def downgrade() -> None:
    op.drop_table("workflow_events")
    op.drop_table("products")
