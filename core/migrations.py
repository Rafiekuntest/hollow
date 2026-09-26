"""
Database Migration System
Lightweight migration runner with version tracking.
Inspired by Alembic but simplified for SQLite.
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Optional

import aiosqlite

from core.hollow import hollow

logger = logging.getLogger("hollow.migrations")

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
MIGRATIONS_DIR.mkdir(exist_ok=True)

# Migration file naming: V{version}__{description}.sql
# Example: V1__initial_schema.sql, V2__add_disabled_cogs.sql
MIGRATION_PATTERN = re.compile(r"^V(\d+)__(.+)\.sql$")


class Migration:
    """Represents a single migration."""

    def __init__(self, version: int, description: str, filepath: Path):
        self.version = version
        self.description = description
        self.filepath = filepath

    @classmethod
    def from_file(cls, filepath: Path) -> Optional["Migration"]:
        match = MIGRATION_PATTERN.match(filepath.name)
        if not match:
            return None
        version = int(match.group(1))
        description = match.group(2).replace("_", " ")
        return cls(version, description, filepath)

    async def apply(self, db: aiosqlite.Connection) -> bool:
        """Apply this migration."""
        try:
            sql = self.filepath.read_text(encoding="utf-8")
            await db.executescript(sql)
            # Record migration
            await db.execute(
                "INSERT INTO schema_migrations (version, description) VALUES (?, ?)",
                (self.version, self.description),
            )
            await db.commit()
            logger.info(f"Applied migration V{self.version}: {self.description}")
            return True
        except Exception as e:
            logger.error(f"Failed to apply migration V{self.version}: {e}")
            return False


class MigrationManager:
    """Manages database migrations."""

    def __init__(self, bot: hollow):
        self.bot = bot
        self.migrations: list[Migration] = []

    def discover_migrations(self) -> list[Migration]:
        """Find all migration files."""
        migrations = []
        for filepath in sorted(MIGRATIONS_DIR.glob("V*.sql")):
            migration = Migration.from_file(filepath)
            if migration:
                migrations.append(migration)
            else:
                logger.warning(f"Skipping invalid migration file: {filepath.name}")
        return sorted(migrations, key=lambda m: m.version)

    async def ensure_migration_table(self) -> None:
        """Create migration tracking table if not exists."""
        await self.bot.db.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                description TEXT NOT NULL,
                applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await self.bot.db.commit()

    async def get_applied_version(self) -> int:
        """Get the latest applied migration version."""
        cursor = await self.bot.db.execute(
            "SELECT MAX(version) as version FROM schema_migrations"
        )
        row = await cursor.fetchone()
        return row["version"] if row and row["version"] else 0

    async def get_pending_migrations(self) -> list[Migration]:
        """Get migrations that haven't been applied yet."""
        applied_version = await self.get_applied_version()
        all_migrations = self.discover_migrations()
        return [m for m in all_migrations if m.version > applied_version]

    async def run_migrations(self) -> bool:
        """Run all pending migrations."""
        await self.ensure_migration_table()

        pending = await self.get_pending_migrations()
        if not pending:
            logger.info("No pending migrations")
            return True

        logger.info(f"Running {len(pending)} pending migration(s)...")

        for migration in pending:
            success = await migration.apply(self.bot.db)
            if not success:
                logger.error(f"Migration V{migration.version} failed, stopping")
                return False

        logger.info(f"All {len(pending)} migration(s) applied successfully")
        return True

    async def create_migration(self, description: str) -> Path:
        """Create a new migration file with next version number."""
        migrations = self.discover_migrations()
        next_version = (max((m.version for m in migrations), default=0)) + 1

        # Sanitize description for filename
        safe_desc = re.sub(r"[^a-zA-Z0-9_]", "_", description.lower())
        safe_desc = re.sub(r"_+", "_", safe_desc).strip("_")

        filename = f"V{next_version}__{safe_desc}.sql"
        filepath = MIGRATIONS_DIR / filename

        template = f"""-- Migration V{next_version}: {description}
-- Generated automatically

-- Add your SQL statements here
-- Example:
-- CREATE TABLE IF NOT EXISTS example (
--     id INTEGER PRIMARY KEY,
--     name TEXT NOT NULL
-- );
"""

        filepath.write_text(template, encoding="utf-8")
        logger.info(f"Created migration: {filepath}")
        return filepath


async def run_migrations(bot: hollow) -> bool:
    """Convenience function to run migrations."""
    manager = MigrationManager(bot)
    return await manager.run_migrations()