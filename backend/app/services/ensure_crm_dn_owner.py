"""CRM-DN: Рахимова Дилноза и Studio Demo — оба владельцы рабочей компании."""

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
    has_dil = "дилноз" in n or "dilnoz" in n
    has_rah = "рахимов" in n or "rakhimov" in n or "rahimov" in n
    return has_dil and has_rah


async def ensure_crm_dn_dilnoza_owner() -> dict[str, int | str]:
    changed_role = 0
    renamed_company = 0
    restored_demo = 0
    crm_dn_company_id: int | None = None

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
            if u.company_id is not None:
                company = await session.get(Company, int(u.company_id))
                if company is not None and (company.name or "").strip() == SANDBOX_COMPANY_NAME:
                    company.name = CRM_DN_COMPANY_NAME
                    renamed_company += 1
                if company is not None and (company.name or "").strip() == CRM_DN_COMPANY_NAME:
                    crm_dn_company_id = int(company.id)
                elif u.company_id is not None and crm_dn_company_id is None:
                    crm_dn_company_id = int(u.company_id)

        # Studio Demo — тоже владелец той же рабочей компании (по просьбе).
        demo_users = list(
            (
                await session.execute(select(User).where(User.email == SANDBOX_EMAIL))
            ).scalars().all()
        )
        for d in demo_users:
            dirty = False
            if crm_dn_company_id is not None and int(d.company_id or 0) != crm_dn_company_id:
                d.company_id = crm_dn_company_id
                dirty = True
            if d.role != UserRole.owner:
                d.role = UserRole.owner
                dirty = True
            if not d.is_active:
                d.is_active = True
                dirty = True
            if not (d.full_name or "").strip():
                d.full_name = "Studio Demo"
                dirty = True
            if dirty:
                restored_demo += 1

        await session.commit()

    stats: dict[str, int | str] = {
        "dilnoza_users": len(dilnoza_users),
        "changed_role": changed_role,
        "renamed_company": renamed_company,
        "restored_demo": restored_demo,
    }
    if changed_role or renamed_company or restored_demo:
        logger.info("ensure_crm_dn_dilnoza_owner: %s", stats)
    return stats
