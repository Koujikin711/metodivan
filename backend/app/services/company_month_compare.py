"""Шесть месяцев кассы для отчёта компании.

Касса визитов — дата сдачи денег (paid_at, иначе начало визита).
Отменённые записи не входят.

Количество («Записи») — только реальные визиты/записи по start_at
(онлайн-запись или стол продаж). Подтверждённые оплаты KPI «Курс /
Протокол / Интенсив…» входят только в выручку по paid_at, без +1 к счётчику
записей — иначе график «Количество» раздувается продажами KPI.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from app.services.sales_kpi_weighted import month_bounds, shift_year_month

SHORT_MONTHS = ["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]
MAX_SERVICES = 7


def _utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _in_span(dt: datetime | None, start: datetime, end: datetime) -> bool:
    stamp = _utc(dt)
    if stamp is None:
        return False
    return start <= stamp < end


def month_starts(year_month, count: int = 6):
    return [shift_year_month(year_month, -back) for back in range(count - 1, -1, -1)]


def month_labels(months) -> list[str]:
    mixed = len({m.year for m in months}) > 1
    labels = []
    for m in months:
        base = SHORT_MONTHS[m.month - 1]
        labels.append(f"{base} {str(m.year)[2:]}" if mixed else base)
    return labels


def kpi_payment_as_visit(
    amount,
    paid_at: datetime | None,
    product_name: str | None,
) -> tuple | None:
    """Оплата KPI → кортеж визита: выручка по paid_at, без start_at (не +1 к «Записи»)."""
    paid = Decimal(str(amount or 0))
    if paid <= 0 or paid_at is None:
        return None
    name = (product_name or "").strip() or "Курс / протокол"
    stamp = _utc(paid_at)
    if stamp is None:
        return None
    # start_at=None → build_month_compare не считает штуки «записей».
    return (paid, "completed", None, stamp, name)


def build_month_compare(year_month, visits: list[tuple]) -> dict:
    """visits: (paid, status, start_at|None, paid_at, direction_name).

    start_at=None — только касса (KPI-оплаты); в «Количество записей» не входит.
    """
    months = month_starts(year_month)
    bounds = [month_bounds(m) for m in months]
    n = len(months)
    revenue = [Decimal("0")] * n
    sales = [0] * n
    by_rev: dict[str, list[Decimal]] = {}
    by_sales: dict[str, list[int]] = {}

    for paid_raw, status_raw, start_at, paid_at, direction in visits:
        if (status_raw or "").strip() == "cancelled":
            continue
        name = (direction or "").strip() or "—"
        paid = Decimal(str(paid_raw or 0))
        cash_at = paid_at or start_at
        rev_row = by_rev.setdefault(name, [Decimal("0")] * n)
        sale_row = by_sales.setdefault(name, [0] * n)
        for i, (start, end) in enumerate(bounds):
            if _in_span(cash_at, start, end) and paid > 0:
                revenue[i] += paid
                rev_row[i] += paid
            # Только реальные визиты с датой приёма/продажи — не синтетика KPI.
            if start_at is not None and _in_span(start_at, start, end):
                sales[i] += 1
                sale_row[i] += 1

    ranked = sorted(
        by_rev,
        key=lambda name: (by_rev[name][-1], sum(by_rev[name], Decimal("0"))),
        reverse=True,
    )
    keep = ranked[:MAX_SERVICES]
    rest = ranked[MAX_SERVICES:]
    services = []
    for name in keep:
        services.append(
            {
                "name": name,
                "revenue": [v.quantize(Decimal("0.01")) for v in by_rev[name]],
                "sales": by_sales.get(name, [0] * n),
            },
        )
    if rest:
        other_rev = [Decimal("0")] * n
        other_sales = [0] * n
        for name in rest:
            for i in range(n):
                other_rev[i] += by_rev[name][i]
                other_sales[i] += by_sales.get(name, [0] * n)[i]
        services.append(
            {
                "name": "Остальные",
                "revenue": [v.quantize(Decimal("0.01")) for v in other_rev],
                "sales": other_sales,
            },
        )

    # Убрать пустые месяцы слева: график с первого месяца с данными (выбранный всегда справа).
    start_i = 0
    for i in range(n - 1):  # last (selected) always kept
        if revenue[i] > 0 or sales[i] > 0:
            start_i = i
            break
        start_i = i + 1

    if start_i > 0:
        months = months[start_i:]
        revenue = revenue[start_i:]
        sales = sales[start_i:]
        for row in services:
            row["revenue"] = row["revenue"][start_i:]
            row["sales"] = row["sales"][start_i:]

    return {
        "labels": month_labels(months),
        "revenue": [v.quantize(Decimal("0.01")) for v in revenue],
        "sales": sales,
        "services": services,
    }
