import asyncio
import html
import logging
import secrets
import string
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Annotated
from urllib.parse import urlparse, urlunparse

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

from app.config import settings
from app.services.tariff import count_company_active_users
from app.services.tariff_effective import effective_tariff_max_active_users
from app.core.deps import CurrentCompanyId, CurrentUser
from app.core.security import hash_password
from app.database import get_db
from app.models import (
    BookingDirection,
    BookingSpecialist,
    EmployeeNameTerm,
    Integration,
    IntegrationProvider,
    Pipeline,
    User,
    UserPipelineAssignment,
    UserRole,
)
from app.routers.booking import resolve_default_booking_direction_id
from app.services.booking_directions import ensure_specialist_direction_link
from app.services.audit import write_audit_event
from app.services.chief_expert_access import assert_owner_admin_or_chief_expert, assert_owner_or_chief_expert
from app.services.debtor_collection import clinic_today
from app.services.finance_integrate import get_gmail_integration
from app.services.green_api_send import send_green_text_async
from app.services.mail import send_email, smtp_misconfig_hint
from app.services.phone_match import whatsapp_e164_digits

# CRM-DN live (не MetodiOne) — fallback, если PUBLIC_APP_URL пуст и нет Host запроса.
_CRM_DN_DEFAULT_APP_URL = "https://crm-dn-koujikin.mia0.amvera.tech"

router = APIRouter(prefix="/employees", tags=["employees"])

# Как у РОП: last_seen_at в пределах окна = «в сети».
_ONLINE_WINDOW = timedelta(minutes=2)


def _is_online(last_seen: datetime | None, *, now: datetime | None = None) -> bool:
    if last_seen is None:
        return False
    n = now or datetime.now(UTC)
    ts = last_seen if last_seen.tzinfo else last_seen.replace(tzinfo=UTC)
    return (n - ts.astimezone(UTC)) <= _ONLINE_WINDOW


class EmployeeRead(BaseModel):
    id: int
    email: str
    phone: str | None = None
    full_name: str | None = None
    role: UserRole
    pipeline_ids: list[int] = Field(default_factory=list)
    specialization: str | None = None
    booking_direction_id: int | None = None
    is_online: bool = False
    last_seen_at: datetime | None = None
    base_salary: Decimal | None = None
    payout_bank: str | None = None
    payroll_only: bool = False
    also_curator: bool = False
    also_expert: bool = False


class InviteEmployeeBody(BaseModel):
    email: str = Field(..., min_length=3, max_length=320)
    """Телефон для WhatsApp-приглашения (номер компании можно переиспользовать после увольнения)."""
    phone: str = Field(..., min_length=7, max_length=32)
    full_name: str = Field(..., min_length=2, max_length=255)
    role: UserRole = UserRole.manager
    pipeline_ids: list[int] = Field(default_factory=list)
    """Для эксперта: подпись в календаре под ФИО (например Невролог)."""
    specialization: str | None = Field(default=None, max_length=255)
    """Для эксперта: направление онлайн-записи (тип слота / колонка календаря)."""
    booking_direction_id: int | None = Field(default=None, ge=1)
    course_streams_enabled: bool = False
    course_stream_max_days: int = Field(15, ge=1, le=90)
    course_stream_min_day_for_next: int = Field(10, ge=1, le=60)
    course_stream_gap_days: int = Field(10, ge=1, le=60)
    also_expert: bool = False
    also_curator: bool = False


class InviteEmployeeResult(BaseModel):
    employee: EmployeeRead
    invite_url: str
    temp_password_sent_to_email: bool
    whatsapp_sent: bool = False
    temp_password: str = ""
    detail: str = ""


class PatchEmployeePipelinesBody(BaseModel):
    pipeline_ids: list[int] = Field(default_factory=list)


_EDITABLE_ROLES = frozenset(
    {
        UserRole.owner,
        UserRole.manager,
        UserRole.rop,
        UserRole.expert,
        UserRole.admin,
        UserRole.administrator,
        UserRole.curator,
        UserRole.accountant,
    }
)


class PatchEmployeeContactBody(BaseModel):
    email: str | None = Field(default=None, min_length=3, max_length=320)
    phone: str | None = Field(default=None, min_length=7, max_length=32)
    full_name: str | None = Field(default=None, min_length=2, max_length=255)
    role: UserRole | None = None
    specialization: str | None = Field(default=None, max_length=255)
    booking_direction_id: int | None = None
    base_salary: Decimal | None = Field(default=None, ge=0)
    payout_bank: str | None = Field(default=None, max_length=32)
    also_curator: bool | None = None
    also_expert: bool | None = None


class PatchEmployeeContactResult(BaseModel):
    employee: EmployeeRead
    email_changed: bool = False
    credentials_email_sent: bool = False


class ResendCredentialsResult(BaseModel):
    employee_id: int
    email_sent: bool
    whatsapp_sent: bool
    detail: str
    password: str


def _rand_password() -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(12))


def _norm_phone(raw: str) -> str:
    digits = "".join(ch for ch in raw if ch.isdigit())
    return digits


async def _employee_read(db: AsyncSession, u: User) -> EmployeeRead:
    rows = await db.execute(
        select(UserPipelineAssignment.pipeline_id).where(
            UserPipelineAssignment.user_id == u.id,
            UserPipelineAssignment.company_id == u.company_id,
        )
    )
    pids = [r[0] for r in rows.all()]
    spec_row = (
        await db.execute(select(BookingSpecialist).where(BookingSpecialist.crm_user_id == u.id).limit(1))
    ).scalars().first()
    sp_text = (spec_row.specialization or "").strip() if spec_row else ""
    return EmployeeRead(
        id=u.id,
        email=u.email,
        phone=u.phone,
        full_name=u.full_name,
        role=u.role,
        pipeline_ids=pids,
        specialization=sp_text or None,
        booking_direction_id=spec_row.direction_id if spec_row else None,
        is_online=_is_online(u.last_seen_at),
        last_seen_at=u.last_seen_at,
        base_salary=u.base_salary,
        payout_bank=u.payout_bank,
        payroll_only=(u.email or "").endswith("@staff.internal"),
        also_curator=bool(getattr(u, "also_curator", False)),
        also_expert=bool(getattr(u, "also_expert", False)),
    )


def _employee_read_cached(
    u: User,
    *,
    pipeline_ids: list[int],
    specialist: BookingSpecialist | None,
) -> EmployeeRead:
    sp_text = (specialist.specialization or "").strip() if specialist else ""
    return EmployeeRead(
        id=u.id,
        email=u.email,
        phone=u.phone,
        full_name=u.full_name,
        role=u.role,
        pipeline_ids=pipeline_ids,
        specialization=sp_text or None,
        booking_direction_id=specialist.direction_id if specialist else None,
        is_online=_is_online(u.last_seen_at),
        last_seen_at=u.last_seen_at,
        base_salary=u.base_salary,
        payout_bank=u.payout_bank,
        payroll_only=(u.email or "").endswith("@staff.internal"),
        also_curator=bool(getattr(u, "also_curator", False)),
        also_expert=bool(getattr(u, "also_expert", False)),
    )


async def _sync_expert_calendar_profile(
    db: AsyncSession,
    *,
    user: User,
    full_name: str,
    phone_norm: str,
    specialization: str,
    booking_direction_id: int,
    course_streams_enabled: bool = False,
    course_stream_max_days: int = 15,
    course_stream_min_day_for_next: int = 10,
    course_stream_gap_days: int = 10,
) -> None:
    d = await db.get(BookingDirection, booking_direction_id)
    if d is None or not d.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Неизвестное или неактивное направление онлайн-записи",
        )

    if d.pipeline_id is None and user.company_id is not None:
        assign_rows = (
            await db.execute(
                select(UserPipelineAssignment.pipeline_id).where(
                    UserPipelineAssignment.user_id == user.id,
                    UserPipelineAssignment.company_id == user.company_id,
                ),
            )
        ).all()
        assigned = {int(r[0]) for r in assign_rows}
        if len(assigned) == 1:
            d.pipeline_id = next(iter(assigned))

    r = await db.execute(select(BookingSpecialist).where(BookingSpecialist.crm_user_id == user.id))
    spec = r.scalars().first()
    if spec is not None:
        spec.full_name = full_name.strip()
        spec.phone = phone_norm or None
        spec.specialization = specialization.strip()
        spec.company_id = user.company_id
        spec.is_active = True
        spec.course_streams_enabled = course_streams_enabled
        spec.course_stream_max_days = course_stream_max_days
        spec.course_stream_min_day_for_next = course_stream_min_day_for_next
        spec.course_stream_gap_days = course_stream_gap_days
        try:
            await ensure_specialist_direction_link(
                db,
                specialist=spec,
                direction_id=booking_direction_id,
                make_primary=True,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        await db.flush()
        return

    mx = await db.scalar(select(func.coalesce(func.max(BookingSpecialist.sort_order), -1)))
    next_sort = int(mx if mx is not None else -1) + 1
    spec = BookingSpecialist(
        company_id=user.company_id,
        full_name=full_name.strip(),
        direction_id=booking_direction_id,
        phone=phone_norm or None,
        specialization=specialization.strip(),
        is_active=True,
        sort_order=next_sort,
        work_start_hour=9,
        work_end_hour=18,
        slot_duration_min=30,
        work_weekdays=[0, 1, 2, 3, 4],
        crm_user_id=user.id,
        course_streams_enabled=course_streams_enabled,
        course_stream_max_days=course_stream_max_days,
        course_stream_min_day_for_next=course_stream_min_day_for_next,
        course_stream_gap_days=course_stream_gap_days,
    )
    db.add(spec)
    await db.flush()
    try:
        await ensure_specialist_direction_link(
            db,
            specialist=spec,
            direction_id=booking_direction_id,
            make_primary=True,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    await db.flush()


def _invite_app_base(request: Request | None = None) -> str:
    """
    Абсолютный URL фронта (без завершающего /).
    Приоритет: PUBLIC_APP_URL → Host запроса (Amvera) → CRM-DN live.
    """
    raw = (settings.public_app_url or "").strip()
    if not raw and request is not None:
        host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or "").split(",")[0].strip()
        if host and "localhost" not in host and "127.0.0.1" not in host:
            proto = (request.headers.get("x-forwarded-proto") or request.url.scheme or "https").split(",")[0].strip()
            if proto not in ("http", "https"):
                proto = "https"
            raw = f"{proto}://{host}"
    if not raw:
        raw = _CRM_DN_DEFAULT_APP_URL
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw
    p = urlparse(raw)
    if p.scheme not in ("http", "https") or not p.netloc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Адрес сайта указан неверно. Пример: https://crm-dn-koujikin.mia0.amvera.tech",
        )
    path = (p.path or "").rstrip("/")
    if path == "/":
        path = ""
    base = urlunparse((p.scheme, p.netloc, path, "", "", "")).rstrip("/")
    if not base:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Адрес сайта указан неверно (нет домена).",
        )
    return base


def _build_invite_url(invite_token: str, request: Request | None = None) -> str:
    return f"{_invite_app_base(request)}/login?invite={invite_token}"


async def _company_smtp_override(db: AsyncSession, company_id: int) -> dict[str, object] | None:
    """Если глобальный SMTP пуст — взять Gmail-интеграцию компании (app_password)."""
    if (settings.smtp_host or "").strip():
        return None
    integ = await get_gmail_integration(db, company_id)
    if integ is None:
        return None
    cfg = integ.config if isinstance(integ.config, dict) else {}
    email_addr = str(cfg.get("email") or cfg.get("gmail_email") or "").strip()
    app_password = str(cfg.get("app_password") or cfg.get("appPassword") or "").strip()
    if not email_addr or not app_password:
        return None
    return {
        "smtp_host": str(cfg.get("smtp_host") or "smtp.gmail.com").strip() or "smtp.gmail.com",
        "smtp_port": int(cfg.get("smtp_port") or 587),
        "smtp_user": email_addr,
        "smtp_password": app_password,
        "smtp_from": email_addr,
    }


def _credentials_email_bodies(
    *,
    email: str,
    temp_password: str,
    invite_url: str,
    intro: str,
    heading: str = "Доступ к CRM",
) -> tuple[str, str]:
    safe_email = html.escape(email)
    safe_pw = html.escape(temp_password)
    safe_url = html.escape(invite_url, quote=True)
    plain = (
        "Здравствуйте!\n\n"
        f"{intro}\n"
        f"Логин: {email}\n"
        f"Пароль: {temp_password}\n"
        f"Вход: {invite_url}\n\n"
        "Старый логин и пароль больше не действуют. После входа рекомендуем сменить пароль."
    )
    html_body = f"""<!DOCTYPE html>
<html><body style="font-family:system-ui,Segoe UI,sans-serif;line-height:1.5;color:#1e293b;background:#f8fafc;padding:24px;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:520px;margin:0 auto;background:#fff;border-radius:16px;box-shadow:0 4px 24px rgba(15,23,42,.08);overflow:hidden;">
    <tr><td style="padding:28px 28px 8px;">
      <h1 style="margin:0;font-size:20px;color:#0f172a;">{html.escape(heading)}</h1>
      <p style="margin:16px 0 0;font-size:15px;">{html.escape(intro)}</p>
    </td></tr>
    <tr><td style="padding:8px 28px;">
      <p style="margin:8px 0;"><strong>Логин:</strong> {safe_email}</p>
      <p style="margin:8px 0;"><strong>Пароль:</strong> <code style="background:#f1f5f9;padding:2px 8px;border-radius:6px;">{safe_pw}</code></p>
    </td></tr>
    <tr><td style="padding:16px 28px 28px;">
      <a href="{safe_url}" style="display:inline-block;background:linear-gradient(135deg,#0f766e,#14b8a6);color:#fff;text-decoration:none;padding:12px 24px;border-radius:12px;font-weight:600;font-size:15px;">Войти в CRM-DN</a>
      <p style="margin:20px 0 0;font-size:13px;color:#64748b;">Если кнопка не открывается, скопируйте ссылку:<br/><span style="word-break:break-all;color:#475569;">{safe_url}</span></p>
    </td></tr>
  </table>
</body></html>"""
    return plain, html_body


def _send_employee_credentials_email(
    to_email: str,
    temp_password: str,
    invite_url: str,
    *,
    intro: str,
    subject: str = "Доступ к CRM-DN",
    heading: str = "Доступ к CRM-DN",
    smtp_override: dict[str, object] | None = None,
) -> tuple[bool, str | None]:
    plain, html_body = _credentials_email_bodies(
        email=to_email,
        temp_password=temp_password,
        invite_url=invite_url,
        intro=intro,
        heading=heading,
    )
    ov = smtp_override or {}
    return send_email(
        to_email,
        subject,
        plain,
        html_body=html_body,
        smtp_host=ov.get("smtp_host") if ov else None,  # type: ignore[arg-type]
        smtp_port=ov.get("smtp_port") if ov else None,  # type: ignore[arg-type]
        smtp_user=ov.get("smtp_user") if ov else None,  # type: ignore[arg-type]
        smtp_password=ov.get("smtp_password") if ov else None,  # type: ignore[arg-type]
        smtp_from=ov.get("smtp_from") if ov else None,  # type: ignore[arg-type]
    )


def _credentials_whatsapp_text(
    *,
    email: str,
    temp_password: str,
    invite_url: str,
) -> str:
    return (
        "Доступ к CRM-DN\n\n"
        f"Логин: {email}\n"
        f"Пароль: {temp_password}\n"
        f"Вход: {invite_url}\n\n"
        "Старый пароль больше не действует. После входа смените пароль."
    )


async def _active_green_integration_for_company(
    db: AsyncSession,
    company_id: int,
    *,
    prefer_pipeline_ids: list[int] | None = None,
) -> Integration | None:
    """Как в чате: сначала Integration.company_id, иначе через воронку компании."""
    prefer = [int(x) for x in (prefer_pipeline_ids or []) if x]
    if prefer:
        row = (
            await db.execute(
                select(Integration)
                .where(
                    Integration.company_id == company_id,
                    Integration.pipeline_id.in_(prefer),
                    Integration.provider == IntegrationProvider.green_api,
                    Integration.is_active.is_(True),
                )
                .order_by(Integration.id.desc())
                .limit(1),
            )
        ).scalars().first()
        if row is not None:
            return row
        row = (
            await db.execute(
                select(Integration)
                .join(Pipeline, Pipeline.id == Integration.pipeline_id)
                .where(
                    Pipeline.company_id == company_id,
                    Integration.pipeline_id.in_(prefer),
                    Integration.provider == IntegrationProvider.green_api,
                    Integration.is_active.is_(True),
                )
                .order_by(Integration.id.desc())
                .limit(1),
            )
        ).scalars().first()
        if row is not None:
            return row
    row = (
        await db.execute(
            select(Integration)
            .where(
                Integration.company_id == company_id,
                Integration.provider == IntegrationProvider.green_api,
                Integration.is_active.is_(True),
            )
            .order_by(Integration.id.desc())
            .limit(1),
        )
    ).scalars().first()
    if row is not None:
        return row
    return (
        await db.execute(
            select(Integration)
            .join(Pipeline, Pipeline.id == Integration.pipeline_id)
            .where(
                Pipeline.company_id == company_id,
                Integration.provider == IntegrationProvider.green_api,
                Integration.is_active.is_(True),
            )
            .order_by(Integration.id.desc())
            .limit(1),
        )
    ).scalars().first()


async def _send_employee_credentials_whatsapp(
    db: AsyncSession,
    *,
    company_id: int,
    phone: str | None,
    email: str,
    temp_password: str,
    invite_url: str,
    prefer_pipeline_ids: list[int] | None = None,
) -> tuple[bool, str | None]:
    digits = whatsapp_e164_digits(phone or "")
    if len(digits) < 9:
        return False, "У сотрудника нет телефона"
    integ = await _active_green_integration_for_company(
        db,
        company_id,
        prefer_pipeline_ids=prefer_pipeline_ids,
    )
    if integ is None:
        return False, "Нет активной WhatsApp (Green API) интеграции"
    text = _credentials_whatsapp_text(
        email=email,
        temp_password=temp_password,
        invite_url=invite_url,
    )
    ok, err, _ = await send_green_text_async(integ.config, f"{digits}@c.us", text)
    if not ok:
        return False, err or "Не удалось отправить WhatsApp"
    return True, None


async def _user_with_phone_except(
    db: AsyncSession,
    phone: str,
    company_id: int,
    *,
    except_user_id: int | None,
) -> User | None:
    q = select(User).where(User.company_id == company_id, User.phone == phone)
    if except_user_id is not None:
        q = q.where(User.id != except_user_id)
    return (await db.execute(q.limit(1))).scalars().first()


@router.get("", response_model=list[EmployeeRead])
async def list_employees(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: CurrentUser,
    company_id: CurrentCompanyId,
) -> list[EmployeeRead]:
    await assert_owner_admin_or_chief_expert(db, current_user, detail="Недостаточно прав для просмотра сотрудников")
    r = await db.execute(select(User).where(User.company_id == company_id, User.is_active.is_(True)).order_by(User.id.desc()))
    users = list(r.scalars().all())
    if not users:
        return []
    user_ids = [int(u.id) for u in users]

    assign_rows = (
        await db.execute(
            select(UserPipelineAssignment.user_id, UserPipelineAssignment.pipeline_id).where(
                UserPipelineAssignment.company_id == company_id,
                UserPipelineAssignment.user_id.in_(user_ids),
            )
        )
    ).all()
    pipes_by_user: dict[int, list[int]] = {uid: [] for uid in user_ids}
    for uid, pid in assign_rows:
        pipes_by_user.setdefault(int(uid), []).append(int(pid))

    spec_rows = (
        await db.execute(select(BookingSpecialist).where(BookingSpecialist.crm_user_id.in_(user_ids)))
    ).scalars().all()
    spec_by_user: dict[int, BookingSpecialist] = {}
    for sp in spec_rows:
        cid = sp.crm_user_id
        if cid is None:
            continue
        uid = int(cid)
        if uid not in spec_by_user:
            spec_by_user[uid] = sp

    return [
        _employee_read_cached(
            u,
            pipeline_ids=pipes_by_user.get(int(u.id), []),
            specialist=spec_by_user.get(int(u.id)),
        )
        for u in users
    ]


_PAYROLL_ROLES = {
    UserRole.manager,
    UserRole.curator,
    UserRole.administrator,
    UserRole.expert,
    UserRole.accountant,
    UserRole.rop,
    UserRole.admin,
}


class PayrollEmployeeCreate(BaseModel):
    full_name: str = Field(..., min_length=2, max_length=255)
    role: UserRole
    specialization: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=32)


@router.post("/for-payroll", response_model=EmployeeRead, status_code=status.HTTP_201_CREATED)
async def create_payroll_employee(
    body: PayrollEmployeeCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: CurrentUser,
    company_id: CurrentCompanyId,
) -> EmployeeRead:
    """Сотрудник только для ведомости: без логина, письма и колонки в записи."""
    await assert_owner_or_chief_expert(db, current_user)
    if body.role not in _PAYROLL_ROLES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Эту роль в ведомость не добавляют")
    name = body.full_name.strip()
    spec = (body.specialization or "").strip()
    if body.role == UserRole.expert and len(spec) < 2:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Укажите специальность: от неё зависит формула ведомости",
        )
    phone = _norm_phone(body.phone or "")
    if body.phone and body.phone.strip() and len(phone) < 7:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Проверьте телефон")
    if phone:
        taken = await _user_with_phone_except(db, phone, company_id, except_user_id=None)
        if taken is not None:
            if taken.is_active:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Телефон уже занят")
            taken.phone = None
            await db.flush()
    user = User(
        company_id=company_id,
        email=f"payroll-{secrets.token_hex(8)}@staff.internal",
        phone=phone or None,
        full_name=name,
        role=body.role,
        hashed_password=hash_password(secrets.token_urlsafe(24)),
        invite_token=None,
        is_active=True,
        accepts_new_leads=False,
    )
    db.add(user)
    await db.flush()
    if body.role == UserRole.expert:
        direction_id = await resolve_default_booking_direction_id(db, company_id)
        if direction_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Нет направления записи, чтобы сохранить специальность",
            )
        db.add(
            BookingSpecialist(
                company_id=company_id,
                full_name=name,
                direction_id=direction_id,
                phone=phone or None,
                specialization=spec,
                is_active=False,
                crm_user_id=user.id,
            )
        )
        await db.flush()
    await write_audit_event(
        db,
        entity_type="employee",
        entity_id=user.id,
        action="payroll_employee_created",
        current_user=current_user,
        details=f"full_name={name}; role={body.role.value}; specialization={spec}",
    )
    employee = await _employee_read(db, user)
    await db.commit()
    return employee


@router.post("/invite", response_model=InviteEmployeeResult, status_code=status.HTTP_201_CREATED)
async def invite_employee(
    body: InviteEmployeeBody,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: CurrentUser,
    company_id: CurrentCompanyId,
) -> InviteEmployeeResult:
    await assert_owner_or_chief_expert(db, current_user)

    if body.role == UserRole.admin and not body.pipeline_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Для роли «Админ» укажите хотя бы одну воронку (онлайн-запись и лиды по направлению)",
        )
    if body.role == UserRole.expert:
        if not body.pipeline_ids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Для эксперта укажите хотя бы одну воронку CRM",
            )
        spec_s = (body.specialization or "").strip()
        if len(spec_s) < 2:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Укажите специальность эксперта (под ФИО в календаре записи, например Консультант)",
            )

    email = body.email.strip().lower()
    if "@" not in email or "." not in email.split("@")[-1]:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Укажите корректный email")
    phone = _norm_phone(body.phone or "")
    if len(phone) < 7:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Укажите телефон — на него уйдёт WhatsApp с доступом",
        )

    # validate pipelines
    if body.pipeline_ids:
        r = await db.execute(
            select(Pipeline.id).where(Pipeline.company_id == company_id, Pipeline.id.in_(body.pipeline_ids))
        )
        ok = {x[0] for x in r.all()}
        if set(body.pipeline_ids) != ok:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown pipeline_id in list")

    _invite_app_base(request)

    existing_by_email = (
        await db.execute(select(User).where(User.company_id == company_id, User.email == email).limit(1))
    ).scalars().first()
    if existing_by_email is not None and existing_by_email.is_active:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    rehire = existing_by_email is not None and not existing_by_email.is_active
    u = existing_by_email if rehire else None

    # Номера компании переходят: у уволенного освобождаем телефон, конфликт только с активным.
    other_phone = await _user_with_phone_except(db, phone, company_id, except_user_id=u.id if u else None)
    if other_phone is not None:
        if other_phone.is_active:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Phone already registered")
        other_phone.phone = None
        await db.flush()

    if not rehire:
        mx = await effective_tariff_max_active_users(db, company_id)
        if mx > 0:
            n_active = await count_company_active_users(db, company_id)
            if n_active >= mx:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Достигнут лимит активных пользователей по тарифу ({mx}).",
                )

    temp_password = _rand_password()
    invite_token = secrets.token_urlsafe(32)

    also_expert = bool(body.also_expert) and body.role != UserRole.expert
    also_curator = bool(body.also_curator) and (
        body.role == UserRole.expert or also_expert
    )
    if bool(body.also_curator) and not also_curator:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Доп. роль куратора доступна только вместе с экспертом",
        )
    if also_expert and body.role not in (
        UserRole.owner,
        UserRole.rop,
        UserRole.manager,
        UserRole.admin,
        UserRole.administrator,
        UserRole.accountant,
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Доп. роль «Эксперт» недоступна для этой основной роли",
        )

    if rehire and u is not None:
        u.phone = phone
        u.full_name = body.full_name.strip()
        u.role = body.role
        u.also_expert = also_expert
        u.also_curator = also_curator
        u.hashed_password = hash_password(temp_password)
        u.invite_token = invite_token
        u.must_change_password = True
        u.is_active = True
        await db.flush()
    else:
        u = User(
            company_id=company_id,
            email=email,
            phone=phone,
            full_name=body.full_name.strip(),
            role=body.role,
            also_expert=also_expert,
            also_curator=also_curator,
            hashed_password=hash_password(temp_password),
            invite_token=invite_token,
            must_change_password=True,
            is_active=True,
        )
        db.add(u)
        await db.flush()

    await db.execute(
        delete(UserPipelineAssignment).where(
            UserPipelineAssignment.user_id == u.id,
            UserPipelineAssignment.company_id == company_id,
        )
    )
    for pid in body.pipeline_ids:
        db.add(UserPipelineAssignment(company_id=company_id, user_id=u.id, pipeline_id=pid))

    await db.flush()
    await db.refresh(u)

    if u.role == UserRole.expert:
        from app.services.crm_space import company_is_sales_mode

        if not await company_is_sales_mode(db, company_id):
            direction_id = body.booking_direction_id
            if direction_id is None:
                direction_id = await resolve_default_booking_direction_id(db, company_id)
            await _sync_expert_calendar_profile(
                db,
                user=u,
                full_name=u.full_name or body.full_name.strip(),
                phone_norm=phone,
                specialization=(body.specialization or "").strip(),
                booking_direction_id=direction_id,
                course_streams_enabled=body.course_streams_enabled,
                course_stream_max_days=body.course_stream_max_days,
                course_stream_min_day_for_next=body.course_stream_min_day_for_next,
                course_stream_gap_days=body.course_stream_gap_days,
            )

    invite_url = _build_invite_url(invite_token, request)
    intro = "Вам восстановили доступ к CRM-DN." if rehire else "Вас пригласили в CRM-DN."
    emp = await _employee_read(db, u)
    smtp_ov = await _company_smtp_override(db, company_id)
    # Оба канала параллельно: письмо (SMTP / Gmail интеграция) + WhatsApp (Green API).
    (email_sent, email_err), (wa_sent, wa_err) = await asyncio.gather(
        asyncio.to_thread(
            _send_employee_credentials_email,
            email,
            temp_password,
            invite_url,
            intro=intro,
            subject="Приглашение в CRM-DN",
            heading="Приглашение в CRM-DN",
            smtp_override=smtp_ov,
        ),
        _send_employee_credentials_whatsapp(
            db,
            company_id=company_id,
            phone=phone,
            email=email,
            temp_password=temp_password,
            invite_url=invite_url,
            prefer_pipeline_ids=emp.pipeline_ids,
        ),
    )
    await write_audit_event(
        db,
        entity_type="user",
        entity_id=u.id,
        action="employee_invited",
        current_user=current_user,
        details=f"email_sent={email_sent}, whatsapp_sent={wa_sent}, role={u.role.value}",
    )

    mail_hint = email_err or smtp_misconfig_hint()
    if email_sent and wa_sent:
        detail = f"Сотрудник создан. Письмо на {email} и WhatsApp на +{whatsapp_e164_digits(phone)} отправлены."
    elif email_sent:
        detail = f"Сотрудник создан. Письмо отправлено на {email}."
        if wa_err:
            detail += f" WhatsApp не ушёл: {wa_err}"
    elif wa_sent:
        detail = f"Сотрудник создан. WhatsApp отправлен. Письмо на {email} не ушло"
        detail += f" ({mail_hint or 'проверьте SMTP'})."
    else:
        detail = "Сотрудник создан. Email и WhatsApp не ушли — скопируйте ссылку и пароль с экрана."
        if mail_hint:
            detail += f" Почта: {mail_hint}."
        if wa_err:
            detail += f" WhatsApp: {wa_err}."

    return InviteEmployeeResult(
        employee=emp,
        invite_url=invite_url,
        temp_password_sent_to_email=email_sent,
        whatsapp_sent=wa_sent,
        temp_password=temp_password,
        detail=detail,
    )


async def _validate_pipeline_ids(db: AsyncSession, company_id: int, pipeline_ids: list[int]) -> list[int]:
    if not pipeline_ids:
        return []
    unique = sorted(set(int(x) for x in pipeline_ids))
    r = await db.execute(
        select(Pipeline.id).where(Pipeline.company_id == company_id, Pipeline.id.in_(unique)),
    )
    ok = {int(x[0]) for x in r.all()}
    if set(unique) != ok:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown pipeline_id in list")
    return unique


def _validate_pipelines_for_role(role: UserRole, pipeline_ids: list[int]) -> None:
    if role in (UserRole.admin, UserRole.expert, UserRole.manager) and not pipeline_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Укажите хотя бы одну воронку для менеджера, админа или эксперта",
        )


async def _remember_employee_name(
    db: AsyncSession,
    *,
    company_id: int,
    user_id: int,
    previous_name: str,
    new_name: str,
) -> None:
    """Старая фамилия остаётся на прошлых месяцах. Новая считается с сегодняшнего дня."""
    today = clinic_today()
    rows = (
        await db.execute(
            select(EmployeeNameTerm).where(
                EmployeeNameTerm.company_id == company_id,
                EmployeeNameTerm.user_id == user_id,
            )
        )
    ).scalars().all()
    by_day = {row.effective_on: row for row in rows}
    if previous_name and not any(day < today for day in by_day):
        db.add(
            EmployeeNameTerm(
                company_id=company_id,
                user_id=user_id,
                full_name=previous_name,
                effective_on=date(2000, 1, 1),
            )
        )
    current = by_day.get(today)
    if current is None:
        db.add(
            EmployeeNameTerm(
                company_id=company_id,
                user_id=user_id,
                full_name=new_name,
                effective_on=today,
            )
        )
    else:
        current.full_name = new_name


@router.patch("/{employee_id}", response_model=PatchEmployeeContactResult)
async def patch_employee_contact(
    employee_id: int,
    body: PatchEmployeeContactBody,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: CurrentUser,
    company_id: CurrentCompanyId,
) -> PatchEmployeeContactResult:
    """Обновить профиль сотрудника: ФИО, контакты, роль, для эксперта — специальность."""
    await assert_owner_or_chief_expert(db, current_user)

    if (
        body.email is None
        and body.phone is None
        and body.full_name is None
        and body.specialization is None
        and body.booking_direction_id is None
        and "role" not in body.model_fields_set
        and "base_salary" not in body.model_fields_set
        and "payout_bank" not in body.model_fields_set
        and "also_curator" not in body.model_fields_set
        and "also_expert" not in body.model_fields_set
    ):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Нет данных для обновления")

    target = await db.get(User, employee_id)
    if target is None or target.company_id != company_id or not target.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Сотрудник не найден")

    if target.role == UserRole.owner and target.id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Контакты другого владельца нельзя изменить",
        )

    email_changed = False
    credentials_sent = False
    changed = False
    new_email = target.email
    new_phone = target.phone
    old_role = target.role

    if "role" in body.model_fields_set and body.role is not None:
        new_role = body.role
        if new_role not in _EDITABLE_ROLES:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Такую роль назначить нельзя")
        if new_role == UserRole.owner and current_user.role not in (UserRole.owner, UserRole.super_owner):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Назначить владельца может только владелец",
            )
        if old_role == UserRole.owner and new_role != UserRole.owner:
            owners_left = await db.scalar(
                select(func.count())
                .select_from(User)
                .where(
                    User.company_id == company_id,
                    User.is_active.is_(True),
                    User.role == UserRole.owner,
                    User.id != target.id,
                )
            )
            if int(owners_left or 0) < 1:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Нельзя снять роль с единственного владельца",
                )
        if new_role == UserRole.expert:
            spec_text = (body.specialization or "").strip()
            if len(spec_text) < 2 and old_role != UserRole.expert:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Для роли «Эксперт» укажите специальность",
                )
        if old_role != new_role:
            changed = True
            target.role = new_role
            if new_role not in (
                UserRole.owner,
                UserRole.rop,
                UserRole.manager,
                UserRole.admin,
                UserRole.administrator,
                UserRole.expert,
                UserRole.accountant,
            ):
                target.also_expert = False
            # also_curator сбрасываем только если нет ни роли эксперта, ни доп. эксперта
            # (флаги из body применим ниже)
            if new_role != UserRole.expert and "also_expert" not in body.model_fields_set:
                if not bool(getattr(target, "also_expert", False)):
                    target.also_curator = False
            elif new_role != UserRole.expert and "also_expert" in body.model_fields_set:
                if not bool(body.also_expert):
                    target.also_curator = False
            if old_role == UserRole.expert and new_role != UserRole.expert:
                spec_row = (
                    await db.execute(
                        select(BookingSpecialist).where(BookingSpecialist.crm_user_id == target.id).limit(1),
                    )
                ).scalars().first()
                if spec_row is not None:
                    spec_row.is_active = False
            if new_role == UserRole.expert:
                from app.services.crm_space import company_is_sales_mode

                if not await company_is_sales_mode(db, company_id):
                    direction_id = body.booking_direction_id
                    if direction_id is None:
                        direction_id = await resolve_default_booking_direction_id(db, company_id)
                    await _sync_expert_calendar_profile(
                        db,
                        user=target,
                        full_name=(body.full_name or target.full_name or "").strip() or target.email,
                        phone_norm=_norm_phone(body.phone or target.phone or ""),
                        specialization=(body.specialization or "").strip(),
                        booking_direction_id=direction_id,
                        course_streams_enabled=False,
                        course_stream_max_days=15,
                        course_stream_min_day_for_next=10,
                        course_stream_gap_days=10,
                    )
            if new_role not in (UserRole.manager, UserRole.admin, UserRole.expert):
                await db.execute(
                    delete(UserPipelineAssignment).where(
                        UserPipelineAssignment.user_id == target.id,
                        UserPipelineAssignment.company_id == company_id,
                    )
                )

    if "also_expert" in body.model_fields_set and body.also_expert is not None:
        if target.role not in (
            UserRole.owner,
            UserRole.rop,
            UserRole.manager,
            UserRole.admin,
            UserRole.administrator,
            UserRole.expert,
            UserRole.accountant,
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Вторая роль «Эксперт» недоступна для этой основной роли",
            )
        if bool(getattr(target, "also_expert", False)) != bool(body.also_expert):
            changed = True
            target.also_expert = bool(body.also_expert)
            if not body.also_expert and target.role != UserRole.expert:
                target.also_curator = False

    if "also_curator" in body.model_fields_set and body.also_curator is not None:
        can_curator = target.role == UserRole.expert or bool(getattr(target, "also_expert", False))
        if body.also_curator and not can_curator:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Доп. роль куратора доступна с экспертом (основная или доп.)",
            )
        if bool(target.also_curator) != bool(body.also_curator):
            changed = True
            target.also_curator = bool(body.also_curator)

    if body.email is not None:
        normalized = body.email.strip().lower()
        if normalized != (target.email or "").strip().lower():
            changed = True
            conflict = (
                await db.execute(
                    select(User).where(
                        User.company_id == company_id,
                        User.email == normalized,
                        User.id != target.id,
                    ).limit(1)
                )
            ).scalars().first()
            if conflict is not None:
                if conflict.is_active:
                    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email уже занят")
                # Уволенный не входит в систему — почту можно отдать действующему сотруднику.
                conflict.email = f"fired-{conflict.id}@inactive.local"
                await db.flush()

            temp_password = _rand_password()
            invite_token = secrets.token_urlsafe(32)
            invite_url = _build_invite_url(invite_token)
            intro = "Для вашего аккаунта в CRM-DN изменён email. Используйте новые данные для входа."
            smtp_ov = await _company_smtp_override(db, company_id)
            credentials_sent, mail_err = _send_employee_credentials_email(
                normalized,
                temp_password,
                invite_url,
                intro=intro,
                subject="Новый логин для CRM-DN",
                heading="Смена email в CRM-DN",
                smtp_override=smtp_ov,
            )
            if not credentials_sent:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        "Не удалось отправить письмо на новый email. Старый логин и пароль остаются."
                        + (f" {mail_err}" if mail_err else "")
                    ),
                )
            target.email = normalized
            target.hashed_password = hash_password(temp_password)
            target.invite_token = invite_token
            target.must_change_password = True
            new_email = normalized
            email_changed = True

    if body.phone is not None:
        phone = _norm_phone(body.phone)
        if len(phone) < 7:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Некорректный телефон")
        if phone != (target.phone or ""):
            changed = True
            other_phone = await _user_with_phone_except(db, phone, company_id, except_user_id=target.id)
            if other_phone is not None:
                if other_phone.is_active:
                    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Телефон уже занят")
                other_phone.phone = None
                await db.flush()
            target.phone = phone
            new_phone = phone
            if target.role == UserRole.expert:
                spec = (
                    await db.execute(
                        select(BookingSpecialist).where(BookingSpecialist.crm_user_id == target.id).limit(1),
                    )
                ).scalars().first()
                if spec is not None:
                    spec.phone = phone

    if body.full_name is not None:
        name = body.full_name.strip()
        if len(name) < 2:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Некорректное ФИО")
        if name != (target.full_name or "").strip():
            changed = True
            previous_name = (target.full_name or "").strip()
            target.full_name = name
            await _remember_employee_name(
                db,
                company_id=company_id,
                user_id=int(target.id),
                previous_name=previous_name,
                new_name=name,
            )
            if target.role == UserRole.expert:
                spec = (
                    await db.execute(
                        select(BookingSpecialist).where(BookingSpecialist.crm_user_id == target.id).limit(1),
                    )
                ).scalars().first()
                if spec is not None:
                    spec.full_name = name

    if target.role == UserRole.expert and body.specialization is not None:
        spec = (
            await db.execute(
                select(BookingSpecialist).where(BookingSpecialist.crm_user_id == target.id).limit(1),
            )
        ).scalars().first()
        if spec is not None:
            new_spec = body.specialization.strip()
            if new_spec != (spec.specialization or "").strip():
                changed = True
                spec.specialization = new_spec

    if "base_salary" in body.model_fields_set and body.base_salary != target.base_salary:
        changed = True
        target.base_salary = body.base_salary
    if "payout_bank" in body.model_fields_set:
        bank = (body.payout_bank or "").strip() or None
        if bank != (target.payout_bank or None):
            changed = True
            target.payout_bank = bank

    if not changed:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Нет изменений")

    try:
        await db.flush()
        await write_audit_event(
            db,
            entity_type="employee",
            entity_id=target.id,
            action="employee_updated",
            current_user=current_user,
            details=(
                f"email_changed={email_changed}, email={new_email}, phone={new_phone}, "
                f"full_name={target.full_name}"
            ),
        )
        await db.refresh(target)
        employee = await _employee_read(db, target)
        await db.commit()
    except HTTPException:
        raise
    except Exception as exc:
        await db.rollback()
        logger.exception("employee profile update failed id=%s", employee_id)
        detail = f"{type(exc).__name__}: {exc}"
        if isinstance(exc, SQLAlchemyError) and getattr(exc, "orig", None) is not None:
            detail = f"{type(exc).__name__}: {exc.orig}"
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=detail,
        ) from exc

    return PatchEmployeeContactResult(
        employee=employee,
        email_changed=email_changed,
        credentials_email_sent=credentials_sent,
    )


@router.post("/{employee_id}/profile", response_model=PatchEmployeeContactResult)
async def post_employee_profile(
    employee_id: int,
    body: PatchEmployeeContactBody,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: CurrentUser,
    company_id: CurrentCompanyId,
) -> PatchEmployeeContactResult:
    """POST-алиас для обновления профиля (прокси/CDN иногда блокируют PATCH)."""
    return await patch_employee_contact(employee_id, body, db, current_user, company_id)


@router.patch("/{employee_id}/pipelines", response_model=EmployeeRead)
async def patch_employee_pipelines(
    employee_id: int,
    body: PatchEmployeePipelinesBody,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: CurrentUser,
    company_id: CurrentCompanyId,
) -> EmployeeRead:
    await assert_owner_or_chief_expert(db, current_user)

    target = await db.get(User, employee_id)
    if target is None or target.company_id != company_id or not target.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Сотрудник не найден")

    if target.role == UserRole.owner:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Воронки владельца не редактируются — у владельца доступ ко всем воронкам",
        )

    pipeline_ids = await _validate_pipeline_ids(db, company_id, body.pipeline_ids)
    _validate_pipelines_for_role(target.role, pipeline_ids)

    old_rows = (
        await db.execute(
            select(UserPipelineAssignment.pipeline_id).where(
                UserPipelineAssignment.user_id == target.id,
                UserPipelineAssignment.company_id == company_id,
            ),
        )
    ).all()
    old_ids = {int(r[0]) for r in old_rows}
    new_ids = set(pipeline_ids)

    removed = old_ids - new_ids
    if removed:
        pipes_to_clear = (
            await db.execute(
                select(Pipeline).where(
                    Pipeline.company_id == company_id,
                    Pipeline.id.in_(removed),
                    Pipeline.intake_manager_user_id == target.id,
                ),
            )
        ).scalars().all()
        for pipe in pipes_to_clear:
            pipe.intake_manager_user_id = None

    try:
        await db.execute(
            delete(UserPipelineAssignment).where(
                UserPipelineAssignment.user_id == target.id,
                UserPipelineAssignment.company_id == company_id,
            ),
        )
        # С autoflush=False delete нужно зафиксировать до insert, иначе возможен unique conflict.
        await db.flush()
        for pid in pipeline_ids:
            db.add(UserPipelineAssignment(company_id=company_id, user_id=target.id, pipeline_id=pid))
        await db.flush()

        await write_audit_event(
            db,
            entity_type="employee",
            entity_id=target.id,
            action="employee_pipelines_updated",
            current_user=current_user,
            details=f"pipeline_ids={pipeline_ids}",
        )

        await db.refresh(target)
        result = await _employee_read(db, target)
        await db.commit()
    except HTTPException:
        raise
    except Exception as exc:
        await db.rollback()
        logger.exception("employee pipelines update failed id=%s", employee_id)
        detail = f"{type(exc).__name__}: {exc}"
        if isinstance(exc, SQLAlchemyError) and getattr(exc, "orig", None) is not None:
            detail = f"{type(exc).__name__}: {exc.orig}"
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=detail,
        ) from exc

    return result


@router.post("/{employee_id}/pipelines/set", response_model=EmployeeRead)
async def post_employee_pipelines(
    employee_id: int,
    body: PatchEmployeePipelinesBody,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: CurrentUser,
    company_id: CurrentCompanyId,
) -> EmployeeRead:
    """POST-алиас для назначения воронок (прокси/CDN иногда блокируют PATCH)."""
    return await patch_employee_pipelines(employee_id, body, db, current_user, company_id)


@router.post("/{employee_id}/resend-credentials", response_model=ResendCredentialsResult)
async def resend_employee_credentials(
    employee_id: int,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: CurrentUser,
    company_id: CurrentCompanyId,
) -> ResendCredentialsResult:
    """Сгенерировать новый временный пароль и отправить логин+пароль на email и в WhatsApp."""
    await assert_owner_or_chief_expert(db, current_user)

    target = await db.get(User, employee_id)
    if target is None or target.company_id != company_id or not target.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Сотрудник не найден")
    if target.role == UserRole.owner and target.id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Нельзя сбросить пароль другого владельца",
        )

    _invite_app_base(request)
    temp_password = _rand_password()
    invite_token = secrets.token_urlsafe(32)
    target.hashed_password = hash_password(temp_password)
    target.invite_token = invite_token
    target.must_change_password = True
    await db.flush()

    invite_url = _build_invite_url(invite_token, request)
    intro = "Вам повторно выслан доступ к CRM-DN. Используйте новые данные для входа."
    smtp_ov = await _company_smtp_override(db, company_id)
    email_sent, email_err = _send_employee_credentials_email(
        target.email,
        temp_password,
        invite_url,
        intro=intro,
        subject="Повторный доступ к CRM-DN",
        heading="Логин и пароль",
        smtp_override=smtp_ov,
    )

    emp = await _employee_read(db, target)
    wa_sent, wa_err = await _send_employee_credentials_whatsapp(
        db,
        company_id=company_id,
        phone=target.phone,
        email=target.email,
        temp_password=temp_password,
        invite_url=invite_url,
        prefer_pipeline_ids=emp.pipeline_ids,
    )

    await write_audit_event(
        db,
        entity_type="user",
        entity_id=target.id,
        action="credentials_resent",
        current_user=current_user,
        details=f"email_sent={email_sent}, whatsapp_sent={wa_sent}",
    )
    await db.commit()

    if email_sent or wa_sent:
        detail = "Пароль записан."
        if email_sent and wa_sent:
            detail += " Отправлено на email и в WhatsApp."
        elif email_sent:
            detail += " Отправлено на email."
        else:
            detail += " Отправлено в WhatsApp."
        if email_sent and not wa_sent and wa_err:
            detail += f" WhatsApp: {wa_err}"
        elif wa_sent and not email_sent:
            detail += f" Письмо не ушло{f': {email_err}' if email_err else ''}."
    else:
        detail = "Пароль записан. Письмо и WhatsApp не ушли — скопируйте пароль с этого экрана."
        if email_err:
            detail += f" Почта: {email_err}."
        if wa_err:
            detail += f" WhatsApp: {wa_err}."

    return ResendCredentialsResult(
        employee_id=int(target.id),
        email_sent=email_sent,
        whatsapp_sent=wa_sent,
        detail=detail,
        password=temp_password,
    )


@router.delete("/{employee_id}", status_code=status.HTTP_204_NO_CONTENT)
async def terminate_employee(
    employee_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: CurrentUser,
    company_id: CurrentCompanyId,
) -> Response:
    await assert_owner_or_chief_expert(db, current_user)
    if employee_id == current_user.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Нельзя уволить самого себя")

    target = await db.get(User, employee_id)
    if target is None or target.company_id != company_id or not target.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Сотрудник не найден")

    if target.role == UserRole.owner:
        owners = await db.scalar(
            select(func.count()).select_from(User).where(User.role == UserRole.owner, User.is_active.is_(True))
            .where(User.company_id == company_id)
        )
        if owners is not None and owners <= 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Нельзя уволить последнего владельца",
            )

    target.is_active = False
    target.invite_token = None
    if target.role == UserRole.manager:
        from app.services.protocol_reassign import reassign_protocols_from_inactive_managers

        await reassign_protocols_from_inactive_managers(db)
    await db.execute(
        delete(UserPipelineAssignment).where(
            UserPipelineAssignment.user_id == target.id,
            UserPipelineAssignment.company_id == company_id,
        )
    )
    if target.role == UserRole.expert:
        for sp in (
            await db.execute(select(BookingSpecialist).where(BookingSpecialist.crm_user_id == target.id))
        ).scalars().all():
            sp.is_active = False
    await db.flush()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/experts")
async def list_experts(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: CurrentUser,
    company_id: CurrentCompanyId,
) -> list[dict[str, object]]:
    await assert_owner_or_chief_expert(db, current_user)
    rows = (
        await db.execute(
            select(User.id, User.email, User.full_name)
            .where(User.company_id == company_id, User.is_active.is_(True), User.role == UserRole.expert)
            .order_by(User.id.desc())
        )
    ).all()
    return [
        {"id": int(uid), "email": str(email), "full_name": (str(fn) if fn else None)}
        for uid, email, fn in rows
    ]

