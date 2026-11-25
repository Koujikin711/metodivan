"""Равная раздача только «молчаливых» лидов (без переписки менеджера и без продаж)."""

from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    ChatMessage,
    ChatThread,
    Lead,
    LeadAuditEvent,
    Pipeline,
    PipelineStage,
    SalesKpiManualSale,
    SystemAuditEvent,
)

# Закрытые исходы и продажи не трогаем при равной раздаче.
_PROTECTED_STAGE_NAMES = frozenset({"Удачно", "Отказ"})


async def lead_ids_with_manager_outbound(
    db: AsyncSession,
    *,
    company_id: int,
    lead_ids: list[int],
) -> set[int]:
    """Лиды, с которыми менеджер уже писал в чат (исходящее сообщение)."""
    if not lead_ids:
        return set()
    out: set[int] = set()
    chunk = 1500
    for i in range(0, len(lead_ids), chunk):
        part = lead_ids[i : i + chunk]
        rows = (
            await db.execute(
                select(ChatThread.lead_id)
                .join(ChatMessage, ChatMessage.thread_id == ChatThread.id)
                .where(
                    ChatThread.company_id == company_id,
                    ChatThread.lead_id.in_(part),
                    ChatMessage.direction == "out",
                )
                .distinct(),
            )
        ).all()
        out.update(int(r[0]) for r in rows if r[0] is not None)
    return out


async def lead_ids_with_sale(
    db: AsyncSession,
    *,
    company_id: int,
    lead_ids: list[int],
) -> set[int]:
    """Лиды с ручной/KPI-продажей (SalesKpiManualSale)."""
    if not lead_ids:
        return set()
    out: set[int] = set()
    chunk = 1500
    for i in range(0, len(lead_ids), chunk):
        part = lead_ids[i : i + chunk]
        rows = (
            await db.execute(
                select(SalesKpiManualSale.lead_id).where(
                    SalesKpiManualSale.company_id == company_id,
                    SalesKpiManualSale.lead_id.in_(part),
                ).distinct(),
            )
        ).all()
        out.update(int(r[0]) for r in rows if r[0] is not None)
    return out


async def lead_ids_on_protected_stages(
    db: AsyncSession,
    *,
    company_id: int,
    lead_ids: list[int],
) -> set[int]:
    """Лиды на стадиях Удачно/Отказ."""
    if not lead_ids:
        return set()
    stage_ids = (
        await db.execute(
            select(PipelineStage.id)
            .outerjoin(Pipeline, Pipeline.id == PipelineStage.pipeline_id)
            .where(
                PipelineStage.name.in_(list(_PROTECTED_STAGE_NAMES)),
                or_(
                    PipelineStage.company_id == company_id,
                    Pipeline.company_id == company_id,
                ),
            ),
        )
    ).scalars().all()
    if not stage_ids:
        return set()
    protected_ids = {int(x) for x in stage_ids}
    out: set[int] = set()
    chunk = 1500
    for i in range(0, len(lead_ids), chunk):
        part = lead_ids[i : i + chunk]
        rows = (
            await db.execute(
                select(Lead.id).where(
                    Lead.company_id == company_id,
                    Lead.id.in_(part),
                    Lead.status_id.in_(protected_ids),
                ),
            )
        ).scalars().all()
        out.update(int(x) for x in rows)
    return out


async def protected_lead_ids(
    db: AsyncSession,
    *,
    company_id: int,
    lead_ids: list[int],
) -> set[int]:
    """Нельзя переносить: писали / продажа / Удачно / Отказ."""
    contacted = await lead_ids_with_manager_outbound(db, company_id=company_id, lead_ids=lead_ids)
    sold = await lead_ids_with_sale(db, company_id=company_id, lead_ids=lead_ids)
    closed = await lead_ids_on_protected_stages(db, company_id=company_id, lead_ids=lead_ids)
    return contacted | sold | closed


async def restore_protected_from_equal_moves(
    db: AsyncSession,
    *,
    company_id: int,
    target_ids: list[int],
    since_hours: int = 72,
) -> tuple[int, list[tuple[int, int | None, int]]]:
    """Вернуть на прежнего менеджера лиды из equal-раздачи, если с ними общались или продали.

    Returns (restored_count, list of (lead_id, from_mid, to_mid) that were restored).
    """
    from datetime import UTC, datetime, timedelta

    since = datetime.now(UTC) - timedelta(hours=max(1, since_hours))
    # Недавние equal-batch id из system audit
    batch_rows = (
        await db.execute(
            select(SystemAuditEvent.id)
            .where(
                SystemAuditEvent.company_id == company_id,
                SystemAuditEvent.action == "leads_redistributed_equal",
                SystemAuditEvent.created_at >= since,
            )
            .order_by(SystemAuditEvent.id.desc())
            .limit(20),
        )
    ).scalars().all()
    batch_ids = [int(x) for x in batch_rows]
    if not batch_ids:
        # fallback: любые manager_reassigned с маркером redistribute-equal
        events = (
            await db.execute(
                select(LeadAuditEvent)
                .where(
                    LeadAuditEvent.action == "manager_reassigned",
                    LeadAuditEvent.created_at >= since,
                    LeadAuditEvent.details.ilike("%(redistribute-equal)%"),
                )
                .order_by(LeadAuditEvent.id.desc())
                .limit(5000),
            )
        ).scalars().all()
    else:
        like_parts = [f"%batch_id={bid}%" for bid in batch_ids]
        events = (
            await db.execute(
                select(LeadAuditEvent)
                .where(
                    LeadAuditEvent.action == "manager_reassigned",
                    LeadAuditEvent.details.ilike("%(redistribute-equal)%"),
                    or_(*[LeadAuditEvent.details.ilike(p) for p in like_parts]),
                )
                .order_by(LeadAuditEvent.id.asc())
                .limit(8000),
            )
        ).scalars().all()

    import re

    re_from = re.compile(r"from_manager_id=(\d+|None)")
    re_to = re.compile(r"to_manager_id=(\d+)")

    # Первое перемещение по lead (исходный from)
    first_move: dict[int, tuple[int | None, int]] = {}
    for ev in events:
        lid = int(ev.lead_id)
        details = ev.details or ""
        m_from = re_from.search(details)
        m_to = re_to.search(details)
        if not m_to:
            continue
        to_mid = int(m_to.group(1))
        raw_from = m_from.group(1) if m_from else None
        from_mid = None if raw_from in (None, "None") else int(raw_from)
        if lid not in first_move:
            first_move[lid] = (from_mid, to_mid)

    if not first_move:
        return 0, []

    lead_ids = list(first_move.keys())
    # текущие менеджеры
    cur: dict[int, int | None] = {}
    for i in range(0, len(lead_ids), 1500):
        part = lead_ids[i : i + 1500]
        rows = (await db.execute(select(Lead.id, Lead.manager_id).where(Lead.id.in_(part)))).all()
        for lid, mid in rows:
            cur[int(lid)] = int(mid) if mid is not None else None

    # Кандидаты: ушли с исходного менеджера после equal-раздачи (даже если потом ещё раз двигали).
    candidates = [
        lid
        for lid, (from_mid, _to_mid) in first_move.items()
        if from_mid is not None and cur.get(lid) is not None and cur.get(lid) != from_mid
    ]
    if not candidates:
        return 0, []

    protected = await protected_lead_ids(db, company_id=company_id, lead_ids=candidates)
    restored: list[tuple[int, int | None, int]] = []
    for lid in candidates:
        if lid not in protected:
            continue
        from_mid, _to_mid = first_move[lid]
        if from_mid is None:
            continue
        lead = await db.get(Lead, lid)
        if lead is None or lead.company_id != company_id:
            continue
        cur_mid = int(lead.manager_id) if lead.manager_id is not None else None
        if cur_mid == from_mid:
            continue
        lead.manager_id = from_mid
        restored.append((lid, from_mid, cur_mid or 0))

    return len(restored), restored


async def selectable_silent_leads(
    db: AsyncSession,
    *,
    company_id: int,
    target_ids: list[int],
    include_unassigned: bool,
) -> list[tuple[int, int | None]]:
    """Лиды для равной раздачи: «Новый лид» / без исходящих, без продаж и закрытых стадий."""
    new_stage_ids = (
        await db.execute(
            select(PipelineStage.id)
            .outerjoin(Pipeline, Pipeline.id == PipelineStage.pipeline_id)
            .where(
                PipelineStage.name == "Новый лид",
                or_(
                    PipelineStage.company_id == company_id,
                    Pipeline.company_id == company_id,
                ),
            ),
        )
    ).scalars().all()
    new_ids = {int(x) for x in new_stage_ids}

    filters = [Lead.company_id == company_id]
    if include_unassigned:
        filters.append(or_(Lead.manager_id.in_(target_ids), Lead.manager_id.is_(None)))
    else:
        filters.append(Lead.manager_id.in_(target_ids))
    # Предпочитаем колонку «Новый лид»; иначе — все, но всё равно отфильтруем protected.
    if new_ids:
        filters.append(Lead.status_id.in_(new_ids))

    rows = (
        await db.execute(
            select(Lead.id, Lead.manager_id).where(*filters).order_by(Lead.id.asc()),
        )
    ).all()
    if not rows:
        return []
    lead_ids = [int(r[0]) for r in rows]
    protected = await protected_lead_ids(db, company_id=company_id, lead_ids=lead_ids)
    return [
        (int(lid), int(mid) if mid is not None else None)
        for lid, mid in rows
        if int(lid) not in protected
    ]
