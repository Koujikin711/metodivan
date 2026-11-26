"""CRM-DN: воронка везде «Маркетинг» (не Sales funnel / Основная)."""

from __future__ import annotations

import logging

from sqlalchemy import select, update

from app.database import AsyncSessionLocal
from app.models import Pipeline

logger = logging.getLogger(__name__)

# Legacy-имена → «Маркетинг» в БД и в API dropdown.
_LEGACY_NAMES = {
    "sales funnel",
    "sales_funnel",
    "salesfunnel",
    "sales",
    "pipeline",
    "sales pipeline",
    "основная",
    "основной",
    "main",
    "default",
}

TARGET = "Маркетинг"


def _norm(name: str | None) -> str:
    return (name or "").strip().lower().replace("ё", "е")


def _should_rename(name: str | None) -> bool:
    key = _norm(name)
    if key == _norm(TARGET):
        return False
    if key in _LEGACY_NAMES:
        return True
    if "funnel" in key and "sales" in key:
        return True
    if key.isascii() and ("funnel" in key or "pipeline" in key):
        return True
    return False


def display_pipeline_name(name: str | None) -> str:
    """Имя для API/ответов: legacy или пусто → Маркетинг."""
    if _should_rename(name) or not (name or "").strip():
        return TARGET
    return str(name).strip()


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
