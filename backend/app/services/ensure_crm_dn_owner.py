"""CRM-DN: Рахимова Дилноза — владелец; Studio Demo не в рабочей команде.

Рабочий CRM-DN часто стартовал из Public Sandbox — фиксируем владельца
и прячем студийную учётку, не трогая остальных сотрудников.
"""

from __future__ import annotations

import logging

from sqlalchemy import func, or_, select

from app.database import AsyncSessionLocal
from app.models import Company, User, UserRole

logger = logging.getLogger(__name__)

SANDBOX_COMPANY_NAME = "Public Sandbox (MetodiOne Studio)"
CRM_DN_COMPANY_NAME = "CRM-DN"
DILNOZA_EMAIL = "dilnazesmm@gmail.com"
SANDBOX_EMAIL = "demo@sandbox.local"


def _name_looks_like_dilnoza(full_name: str | None) -> bool:
    n = (full_name or "").casefold()
    if not n:
        return False
    # «Рахимова Дилноза» / «Дилноза Рахимова» и похожие написания
    has_dil = "дилноз" in n or "dilnoz" in n
    has_rah = "рахимов" in n or "rakhimov" in n or "rahimov" in n
    return has_dil and has_rah


async def ensure_crm_dn_dilnoza_owner() -> dict[str, int | str]:
    changed_role = 0
    renamed_company = 0
    deactivated_demo = 0

    async with AsyncSessionLocal() as session:
        dilnoza_users = list(
            (
                await session.execute(
                    select(User).where(
                        or_(
                            func.lower(User.email) == DILNOZA_EMAIL,
                            User.full_name.ilike("%Дилноз%"),
                            User.full_name.ilike("%Dilnoz%"),
                        ),
                    ),
                )
            ).scalars().all()
        )
        dilnoza_users = [
            u
            for u in dilnoza_users
            if (u.email or "").strip().lower() == DILNOZA_EMAIL or _name_looks_like_dilnoza(u.full_name)
        ]

        for u in dilnoza_users:
            if u.role != UserRole.owner:
                u.role = UserRole.owner
                changed_role += 1
            if not u.is_active:
                u.is_active = True
            # Рабочий CRM-DN: если компания ещё sandbox — переименуем
            if u.company_id is not None:
                company = await session.get(Company, int(u.company_id))
                if company is not None and (company.name or "").strip() == SANDBOX_COMPANY_NAME:
                    company.name = CRM_DN_COMPANY_NAME
                    renamed_company += 1

        # Studio Demo не нужен рядом с живой командой Dilnoza
        if dilnoza_users:
            demo_users = list(
                (
                    await session.execute(select(User).where(User.email == SANDBOX_EMAIL))
                ).scalars().all()
            )
            for d in demo_users:
                if d.is_active:
                    d.is_active = False
                    deactivated_demo += 1

        await session.commit()

    stats: dict[str, int | str] = {
        "dilnoza_users": len(dilnoza_users),
        "changed_role": changed_role,
        "renamed_company": renamed_company,
        "deactivated_demo": deactivated_demo,
    }
    if changed_role or renamed_company or deactivated_demo:
        logger.info("ensure_crm_dn_dilnoza_owner: %s", stats)
    return stats
