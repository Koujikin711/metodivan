"""CRM-DN: продукты онлайн-записи → строки плана KPI продаж на текущий месяц.

Без плана в «KPI → План / Пакеты» нельзя оформить продажу с оплатой
(Интенсив, Курс «Мисли Дилноза», Консультация).
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models import BookingDirection, Pipeline, SalesKpiPlanItem, SalesKpiPlanItemService

logger = logging.getLogger(__name__)

CRM_DN_PRODUCT_NAMES = (
    "Консультация",
    "Интенсив",
    "Курс «Мисли Дилноза»",
)


def _month_start(d: date) -> date:
    return date(d.year, d.month, 1)


async def ensure_crm_dn_sales_plan_items() -> dict[str, int]:
    """Создать недостающие plan items по продуктам CRM-DN для каждой воронки."""
    created = 0
    linked = 0
    async with AsyncSessionLocal() as session:
        pipelines = list((await session.execute(select(Pipeline))).scalars().all())
        ym = _month_start(date.today())
        for pipe in pipelines:
            company_id = int(pipe.company_id) if pipe.company_id is not None else None
            if company_id is None:
                continue
            pipeline_id = int(pipe.id)

            directions = list(
                (
                    await session.execute(
                        select(BookingDirection).where(
                            BookingDirection.company_id == company_id,
                            BookingDirection.is_active.is_(True),
                        ),
                    )
                ).scalars().all()
            )
            by_name = {(d.name or "").strip().casefold(): d for d in directions}

            existing = list(
                (
                    await session.execute(
                        select(SalesKpiPlanItem).where(
                            SalesKpiPlanItem.company_id == company_id,
                            SalesKpiPlanItem.pipeline_id == pipeline_id,
                            SalesKpiPlanItem.year_month == ym,
                        ),
                    )
                ).scalars().all()
            )
            existing_names = {(i.name or "").strip().casefold() for i in existing}
            sort_base = max((int(i.sort_order or 0) for i in existing), default=-1) + 1

            for offset, product_name in enumerate(CRM_DN_PRODUCT_NAMES):
                key = product_name.casefold()
                direction = by_name.get(key)
                if key in existing_names:
                    # Уже есть строка — привяжем direction/service, если пусто
                    item = next(
                        (i for i in existing if (i.name or "").strip().casefold() == key),
                        None,
                    )
                    if item is not None and direction is not None:
                        if item.direction_id is None:
                            item.direction_id = int(direction.id)
                            item.source_type = "direction"
                            linked += 1
                        has_svc = (
                            await session.execute(
                                select(SalesKpiPlanItemService.id).where(
                                    SalesKpiPlanItemService.plan_item_id == int(item.id),
                                    SalesKpiPlanItemService.direction_id == int(direction.id),
                                ).limit(1),
                            )
                        ).scalar_one_or_none()
                        if has_svc is None:
                            session.add(
                                SalesKpiPlanItemService(
                                    plan_item_id=int(item.id),
                                    direction_id=int(direction.id),
                                ),
                            )
                            linked += 1
                    continue

                item = SalesKpiPlanItem(
                    company_id=company_id,
                    pipeline_id=pipeline_id,
                    year_month=ym,
                    name=product_name,
                    plan_qty=0,
                    weight_percent=Decimal("0"),
                    manager_percent=Decimal("10"),
                    source_type="direction" if direction is not None else "manual",
                    direction_id=int(direction.id) if direction is not None else None,
                    sort_order=sort_base + offset,
                )
                session.add(item)
                await session.flush()
                if direction is not None:
                    session.add(
                        SalesKpiPlanItemService(
                            plan_item_id=int(item.id),
                            direction_id=int(direction.id),
                        ),
                    )
                created += 1

        await session.commit()

    stats = {"created": created, "linked": linked}
    if created or linked:
        logger.info("ensure_crm_dn_sales_plan_items: %s", stats)
    return stats
