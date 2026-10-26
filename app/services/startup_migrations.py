"""Apply committed Alembic migrations before the server accepts requests."""
import asyncio
import logging
from pathlib import Path
import subprocess
import sys

logger = logging.getLogger(__name__)


def _upgrade():
    root = Path(__file__).resolve().parents[2]
    logger.info("Checking database migrations before startup")
    try:
        subprocess.run(
            [sys.executable, "-m", "alembic", "-c", str(root / "alembic.ini"), "upgrade", "head"],
            cwd=str(root), check=True, timeout=300,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        logger.error("Database migration failed; server startup is stopped. See migration output above.")
        raise RuntimeError("Database migration failed; server will not start") from exc
    logger.info("Database migrations completed; starting server")


async def apply_startup_migrations():
    await asyncio.to_thread(_upgrade)
