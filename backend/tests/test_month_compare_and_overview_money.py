from datetime import UTC, datetime
from decimal import Decimal

from app.services.analytics_overview_money import overview_visit_money, prior_visit_debt
from app.services.company_month_compare import (
    build_month_compare,
    kpi_payment_as_visit,
    kpi_sale_unit_as_visit,
)


def test_overview_skips_course_package_and_cancelled():
    paid, debt = overview_visit_money(
        [
            (Decimal("1000"), Decimal("1000"), "completed", "Массаж"),
            (Decimal("5800"), Decimal("17000"), "completed", "Курс"),
            (Decimal("300"), Decimal("300"), "cancelled", "ТМС"),
            (Decimal("500"), Decimal("800"), "booked", "Курс 15"),
        ],
    )
    assert paid == Decimal("1500")
    assert debt == Decimal("300")


def test_prior_debt_is_the_gap_under_the_card():
    assert prior_visit_debt(Decimal("195204"), Decimal("183434")) == Decimal("11770.00")
    assert prior_visit_debt(Decimal("100"), Decimal("100")) == Decimal("0")
    assert prior_visit_debt(Decimal("10"), Decimal("12")) == Decimal("0")


def test_month_compare_uses_cash_date_and_skips_cancelled():
    may = datetime(2026, 5, 10, tzinfo=UTC)
    october_visit = datetime(2026, 10, 4, tzinfo=UTC)
    october_cash = datetime(2026, 10, 20, tzinfo=UTC)
    built = build_month_compare(
        datetime(2026, 10, 1).date(),
        [
            (Decimal("100"), "completed", may, may, "Массаж"),
            (Decimal("50"), "cancelled", october_visit, october_cash, "Массаж"),
            (Decimal("200"), "completed", october_visit, october_cash, "ТМС"),
            (Decimal("40"), "booked", datetime(2026, 9, 2, tzinfo=UTC), october_cash, "Остеопат"),
        ],
    )
    assert built["labels"][-1] == "окт"
    assert built["revenue"][-1] == Decimal("240.00")
    assert built["sales"][-1] == 1
    assert built["revenue"][0] == Decimal("100.00")
    names = [row["name"] for row in built["services"]]
    assert "Массаж" in names
    assert "ТМС" in names


def test_month_compare_includes_kpi_protocol_payments():
    """Выручка — оплаты KPI; штуки — порог 25% (sale unit) + онлайн-запись."""
    october_cash = datetime(2026, 10, 15, tzinfo=UTC)
    august_cash = datetime(2026, 8, 12, tzinfo=UTC)
    booking = (Decimal("5800"), "completed", october_cash, october_cash, "Протокол")
    kpi_oct = kpi_payment_as_visit(Decimal("12000"), october_cash, "Протокол")
    kpi_aug = kpi_payment_as_visit(Decimal("30000"), august_cash, "Протокол")
    unit_oct = kpi_sale_unit_as_visit(october_cash, "Протокол")
    unit_aug = kpi_sale_unit_as_visit(august_cash, "Протокол")
    assert kpi_oct is not None and kpi_aug is not None
    assert kpi_oct[2] is None
    assert unit_oct is not None and unit_aug is not None
    built = build_month_compare(
        datetime(2026, 10, 1).date(),
        [booking, kpi_oct, kpi_aug, unit_oct, unit_aug],
    )
    proto = next(row for row in built["services"] if row["name"] == "Протокол")
    assert proto["revenue"][-1] == Decimal("17800.00")  # окт: запись 5800 + KPI 12000
    assert built["labels"][0] == "авг"
    assert proto["revenue"][0] == Decimal("30000.00")
    assert built["revenue"][-1] == Decimal("17800.00")
    # Окт: 1 запись + 1 KPI-продажа; авг: 1 KPI-продажа.
    assert built["sales"][-1] == 2
    assert proto["sales"][-1] == 2
    assert built["sales"][0] == 1
    assert proto["sales"][0] == 1


def test_month_compare_intensiv_payments_alone_do_not_inflate_count():
    """Доплаты Интенсив дают выручку, но не штуки — штуки только через sale unit."""
    nov_cash = datetime(2026, 11, 10, tzinfo=UTC)
    pays = [
        kpi_payment_as_visit(Decimal("7650"), nov_cash, "Интенсив"),
        kpi_payment_as_visit(Decimal("500"), datetime(2026, 11, 12, tzinfo=UTC), "Интенсив"),
    ]
    built = build_month_compare(
        datetime(2026, 11, 1).date(),
        [r for r in pays if r is not None],
    )
    assert built["revenue"] == [Decimal("8150.00")]
    assert built["sales"] == [0]


def test_month_compare_intensiv_sale_units_show_sold_qty():
    """24 продажи Интенсив (порог 25%) → количество 24, выручка отдельно из оплат."""
    nov = datetime(2026, 11, 5, tzinfo=UTC)
    units = [kpi_sale_unit_as_visit(nov, "Интенсив") for _ in range(24)]
    pay = kpi_payment_as_visit(Decimal("25140"), nov, "Интенсив")
    visits = [r for r in [*units, pay] if r is not None]
    built = build_month_compare(datetime(2026, 11, 1).date(), visits)
    assert built["labels"] == ["ноя"]
    assert built["sales"] == [24]
    assert built["services"][0]["name"] == "Интенсив"
    assert built["services"][0]["sales"] == [24]
    assert built["revenue"] == [Decimal("25140.00")]


def test_month_compare_trims_leading_empty_months():
    """Если продажи только в ноябре — не рисуем пустые июн–окт."""
    nov_cash = datetime(2026, 11, 10, tzinfo=UTC)
    built = build_month_compare(
        datetime(2026, 11, 1).date(),
        [
            (Decimal("7650"), "completed", nov_cash, nov_cash, "Интенсив"),
        ],
    )
    assert built["labels"] == ["ноя"]
    assert built["revenue"] == [Decimal("7650.00")]
    assert built["sales"] == [1]
    assert len(built["services"][0]["revenue"]) == 1
