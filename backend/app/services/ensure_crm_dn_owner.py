"""CRM-DN: вернуть Studio Demo активным владельцем. Роль Дилнозы не трогаем."""

from __future__ import annotations

import logging

from sqlalchemy import select, text

from app.database import AsyncSessionLocal
from app.models import User, UserRole

logger = logging.getLogger(__name__)

SANDBOX_EMAIL = "demo@sandbox.local"


async def ensure_crm_dn_dilnoza_owner() -> dict[str, int]:
    """Только Studio Demo: is_active + owner. Дилнозу / других не меняем."""
    restored = 0
    async with AsyncSessionLocal() as session:
        # Надёжно на SQLite: прямой UPDATE (обходит зависания ORM/enum).
        res = await session.execute(
            text(
                "UPDATE users SET is_active = 1, role = 'owner', "
                "full_name = COALESCE(NULLIF(TRIM(full_name), ''), 'Studio Demo') "
                "WHERE lower(email) = :email"
            ),
            {"email": SANDBOX_EMAIL},
        )
        restored = int(res.rowcount or 0)
        await session.commit()

        demo = (
            await session.execute(select(User).where(User.email == SANDBOX_EMAIL).limit(1))
        ).scalars().first()
        if demo is not None and demo.role != UserRole.owner:
            demo.role = UserRole.owner
            demo.is_active = True
            await session.commit()
            restored = max(restored, 1)

    if restored:
        logger.info("ensure_studio_demo_owner: restored=%s", restored)
    return {"restored_demo": restored}
