import re
import secrets
import string

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User


def _slugify(s: str) -> str:
    s = (s or "").lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s[:48] or "business"


def _suffix() -> str:
    alphabet = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(4))


async def ensure_user_slug(db: AsyncSession, user: User) -> str:
    """Generate and persist a permanent order-link slug for this user, once."""
    if user.slug:
        return user.slug
    base = _slugify(user.business_name or user.full_name or "business")
    for _ in range(20):
        candidate = f"{base}-{_suffix()}"
        r = await db.execute(select(User.id).where(User.slug == candidate))
        if r.scalar_one_or_none() is None:
            user.slug = candidate
            await db.commit()
            await db.refresh(user)
            return candidate
    raise RuntimeError("Could not generate a unique slug after 20 attempts")
