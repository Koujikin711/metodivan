"""CRM-DN: воронка везде «Маркетинг» (не Sales funnel / Основная)."""

from __future__ import annotations

import logging

from sqlalchemy import select, update

from app.database import AsyncSessionLocal
from app.models import Pipeline

logger = logging.getLogger(__name__)

# Только английское legacy-имя с прода; «Основная» не трогаем в БД —
# в UI всё равно показывается как «Маркетинг».
_LEGACY_NAMES = {
    "sales funnel",
    "sales_funnel",
    "salesfunnel",
}

TARGET = "Маркетинг"


def _norm(name: str | None) -> str:
    return (name or "").strip().lower().replace("ё", "е")


def _should_rename(name: str | None) -> bool:
    key = _norm(name)
    if key in _LEGACY_NAMES or key == _norm(TARGET):
        return key != _norm(TARGET) and key in _LEGACY_NAMES
    if "funnel" in key and "sales" in key:
        return True
    if key in {"sales", "pipeline", "sales pipeline"}:
        return True
    return False


async def ensure_crm_dn_pipeline_marketing_name() -> int:
    """Переименовать legacy-имена воронок в «Маркетинг». Возвращает число обновлений."""
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(select(Pipeline.id, Pipeline.name))).all()
        ids = [int(pid) for pid, name in rows if _should_rename(str(name))]
        if not ids:
            return 0
        await session.execute(update(Pipeline).where(Pipeline.id.in_(ids)).values(name=TARGET))
        await session.commit()
        logger.info("CRM-DN pipeline rename → %s: %s row(s)", TARGET, len(ids))
        return len(ids)
