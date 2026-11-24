"""Убрать студийный sandbox из рабочих компаний.

Studio Demo и лиды с @example.com — артефакты Public Sandbox /demo-login.
В клиентских компаниях пользователя деактивируем, демо-лиды удаляем.
Живые лиды (без @example.com) не трогаем.
"""

from __future__ import annotations

import logging

from sqlalchemy import delete, false, or_, select

from app.database import AsyncSessionLocal
from app.models import (
    BookingAppointment,
    ChatThread,
    Company,
    Deal,
    FinanceJournalEntry,
    Lead,
    LeadAuditEvent,
    Task,
    User,
)

logger = logging.getLogger(__name__)

SANDBOX_COMPANY_NAME = "Public Sandbox (MetodiOne Studio)"
SANDBOX_EMAIL = "demo@sandbox.local"


async def purge_demo_artifacts_from_client_companies() -> dict[str, int]:
    """Скрыть Studio Demo и удалить демо-лиды (@example.com) вне sandbox."""
    deactivated_users = 0
    deleted_leads = 0

    async with AsyncSessionLocal() as session:
        sandbox_ids = {
            int(cid)
            for cid in (
                await session.execute(
                    select(Company.id).where(Company.name == SANDBOX_COMPANY_NAME),
                )
            ).scalars().all()
        }

        demo_users = list(
            (
                await session.execute(select(User).where(User.email == SANDBOX_EMAIL))
            ).scalars().all()
        )
        for u in demo_users:
            cid = int(u.company_id) if u.company_id is not None else None
            if cid is not None and cid in sandbox_ids:
                continue
            if u.is_active:
                u.is_active = False
                deactivated_users += 1

        demo_rows = list(
            (
                await session.execute(
                    select(Lead.id, Lead.company_id).where(Lead.email.ilike("%@example.com")),
                )
            ).all()
        )
        # company_id → lead ids
        by_company: dict[int, list[int]] = {}
        for lid, cid in demo_rows:
            if cid is None:
                continue
            company_id = int(cid)
            if company_id in sandbox_ids:
                continue
            by_company.setdefault(company_id, []).append(int(lid))

        for company_id, lead_ids in by_company.items():
            deal_ids = list(
                (
                    await session.execute(
                        select(Deal.id).where(
                            Deal.company_id == company_id,
                            Deal.lead_id.in_(lead_ids),
                        ),
                    )
                ).scalars().all()
            )
            await session.execute(
                delete(FinanceJournalEntry).where(
                    FinanceJournalEntry.company_id == company_id,
                    or_(
                        FinanceJournalEntry.related_lead_id.in_(lead_ids),
                        FinanceJournalEntry.related_deal_id.in_(deal_ids) if deal_ids else false(),
                    ),
                ),
            )
            await session.execute(
                delete(BookingAppointment).where(
                    BookingAppointment.company_id == company_id,
                    BookingAppointment.lead_id.in_(lead_ids),
                ),
            )
            await session.execute(
                delete(ChatThread).where(
                    ChatThread.company_id == company_id,
                    ChatThread.lead_id.in_(lead_ids),
                ),
            )
            await session.execute(
                delete(Task).where(
                    Task.company_id == company_id,
                    Task.related_lead_id.in_(lead_ids),
                ),
            )
            await session.execute(
                delete(Deal).where(
                    Deal.company_id == company_id,
                    Deal.lead_id.in_(lead_ids),
                ),
            )
            await session.execute(
                delete(LeadAuditEvent).where(
                    LeadAuditEvent.company_id == company_id,
                    LeadAuditEvent.lead_id.in_(lead_ids),
                ),
            )
            res = await session.execute(delete(Lead).where(Lead.id.in_(lead_ids)))
            deleted_leads += int(res.rowcount or 0)

        await session.commit()

    stats = {
        "deactivated_users": deactivated_users,
        "deleted_leads": deleted_leads,
    }
    if any(stats.values()):
        logger.info("purge_demo_artifacts: %s", stats)
    return stats
