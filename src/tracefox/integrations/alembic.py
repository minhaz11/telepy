"""Alembic / Flask-Migrate support — the counterpart of ``php artisan telescope:install``.

    tracefox install                  # writes migrations/versions/<rev>_create_tracefox_tables.py
    alembic upgrade head              # or: flask db upgrade

In ``env.py`` keep autogenerate from dropping the tables:

    from tracefox.integrations.alembic import include_object
    context.configure(..., include_object=include_object)
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime
from typing import Any, Optional

TABLE_PREFIX = "tracefox_"

MIGRATION = '''"""{message}

Revision ID: {revision}
Revises: {down_revision}
Create Date: {created}
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = {revision!r}
down_revision = {down_revision!r}
branch_labels = None
depends_on = None


def _missing(name):
    return not sa.inspect(op.get_bind()).has_table(name)


def upgrade():
    if _missing("tracefox_entries"):
        op.create_table(
            "tracefox_entries",
            sa.Column("sequence", sa.BigInteger().with_variant(sa.Integer, "sqlite"), primary_key=True, autoincrement=True),
            sa.Column("uuid", sa.String(36), nullable=False, unique=True),
            sa.Column("batch_id", sa.String(36), nullable=False),
            sa.Column("family_hash", sa.String(64), nullable=True),
            sa.Column("should_display_on_index", sa.Boolean(), nullable=False),
            sa.Column("type", sa.String(20), nullable=False),
            sa.Column("content", sa.Text().with_variant(mysql.LONGTEXT(), "mysql"), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
        )
        op.create_index("tracefox_entries_batch_id_index", "tracefox_entries", ["batch_id"])
        op.create_index("tracefox_entries_family_hash_index", "tracefox_entries", ["family_hash"])
        op.create_index("tracefox_entries_created_at_index", "tracefox_entries", ["created_at"])
        op.create_index("tracefox_entries_type_should_display_on_index_index", "tracefox_entries", ["type", "should_display_on_index"])
    if _missing("tracefox_entries_tags"):
        op.create_table(
            "tracefox_entries_tags",
            sa.Column("entry_uuid", sa.String(36), primary_key=True),
            sa.Column("tag", sa.String(191), primary_key=True),
        )
        op.create_index("tracefox_entries_tags_tag_index", "tracefox_entries_tags", ["tag"])
    if _missing("tracefox_monitoring"):
        op.create_table("tracefox_monitoring", sa.Column("tag", sa.String(191), primary_key=True))
    if _missing("tracefox_settings"):
        op.create_table(
            "tracefox_settings",
            sa.Column("key", sa.String(191), primary_key=True),
            sa.Column("value", sa.Text(), nullable=True),
        )


def downgrade():
    for name in ("tracefox_settings", "tracefox_monitoring", "tracefox_entries_tags", "tracefox_entries"):
        if not _missing(name):
            op.drop_table(name)
'''


def include_object(obj: Any, name: Optional[str], type_: str, reflected: bool, compare_to: Any) -> bool:
    """Alembic ``include_object`` hook that leaves the tracefox tables (and their indexes) alone."""
    table = name if type_ == "table" else getattr(getattr(obj, "table", None), "name", None)
    return not (table or "").startswith(TABLE_PREFIX)


def find_script_directory(directory: Optional[str] = None, config_file: str = "alembic.ini") -> str:
    """The Alembic script directory: explicit, from alembic.ini, or a conventional folder."""
    if directory:
        return directory
    if os.path.exists(config_file):
        from alembic.config import Config

        location = Config(config_file).get_main_option("script_location")
        if location:
            return location.replace("%(here)s", os.path.dirname(os.path.abspath(config_file)))
    for candidate in ("migrations", "alembic"):
        if os.path.isfile(os.path.join(candidate, "env.py")):
            return candidate
    raise FileNotFoundError(
        "No Alembic migrations found. Run inside your project (next to alembic.ini), or pass --directory."
    )


def install(directory: Optional[str] = None, message: str = "create tracefox tables") -> Optional[str]:
    """Write the migration that creates the tracefox tables; returns its path, or None if already installed."""
    from alembic.script import ScriptDirectory

    script_dir = find_script_directory(directory)
    script = ScriptDirectory(script_dir)
    for rev in script.walk_revisions():
        if rev.path and "tracefox_entries" in open(rev.path, encoding="utf-8").read():
            return None
    heads = script.get_heads()
    if len(heads) > 1:
        raise RuntimeError(f"Alembic has multiple heads ({', '.join(heads)}); merge them before installing tracefox.")
    revision = uuid.uuid4().hex[:12]
    versions = os.path.join(script_dir, "versions")
    os.makedirs(versions, exist_ok=True)
    path = os.path.join(versions, f"{revision}_create_tracefox_tables.py")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(MIGRATION.format(
            message=message, revision=revision, down_revision=heads[0] if heads else None,
            created=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ))
    return path
