import secrets
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.config import settings
from app.services.chief_expert_access import assert_owner_admin_or_chief_expert
from app.core.deps import CurrentCompanyId, CurrentUser
from app.database import get_db
from app.models import Company, Integration, IntegrationProvider, Pipeline, PipelineStage, TariffPlan, UserRole
from app.services.background_events import list_background_events
from app.services.finance_integrate import get_gmail_integration
from app.services.mail import send_email
from app.services.demo_provision import provision_demo_account
from app.schemas.email_types import RelaxedEmailStr
from app.services.tariff import count_company_active_users, count_company_integrations

router = APIRouter(prefix="/system", tags=["system"])


class SmtpConfigRead(BaseModel):
    host: str
    port: int
    user: str
    from_email: str
    has_password: bool
    public_app_url: str
    public_api_base_url: str
    source: str = Field(description="env | company_gmail | none")


class SmtpConfigPatch(BaseModel):
    host: str = Field(default="smtp.gmail.com", max_length=255)
    port: int = Field(default=587, ge=1, le=65535)
    user: str = Field(..., min_length=3, max_length=320)
    from_email: str | None = Field(default=None, max_length=320)
    password: str | None = Field(
        default=None,
        max_length=256,
        description="Пароль приложения Gmail. Пусто = оставить прежний.",
    )


class SmtpTestBody(BaseModel):
    to_email: str = Field(..., min_length=3, max_length=320)


class DemoRequestBody(BaseModel):
    full_name: str = Field(..., min_length=2, max_length=255)
    phone: str = Field(..., min_length=7, max_length=32)
    email: RelaxedEmailStr
    message: str | None = Field(default=None, max_length=2000)


async def _resolve_smtp(db: AsyncSession, company_id: int) -> tuple[dict[str, Any], str]:
    """Env SMTP имеет приоритет; иначе Gmail-интеграция компании."""
    if (settings.smtp_host or "").strip():
        return (
            {
                "smtp_host": (settings.smtp_host or "").strip(),
                "smtp_port": int(settings.smtp_port or 587),
                "smtp_user": (settings.smtp_user or "").strip(),
                "smtp_password": (settings.smtp_password or "").strip(),
                "smtp_from": (settings.smtp_from or settings.smtp_user or "").strip(),
            },
            "env",
        )
    integ = await get_gmail_integration(db, company_id)
    if integ is not None and isinstance(integ.config, dict):
        cfg = integ.config
        email_addr = str(cfg.get("email") or cfg.get("gmail_email") or "").strip()
        app_password = str(cfg.get("app_password") or cfg.get("appPassword") or "").strip()
        if email_addr:
            return (
                {
                    "smtp_host": str(cfg.get("smtp_host") or "smtp.gmail.com").strip() or "smtp.gmail.com",
                    "smtp_port": int(cfg.get("smtp_port") or 587),
                    "smtp_user": email_addr,
                    "smtp_password": app_password,
                    "smtp_from": str(cfg.get("from_email") or email_addr).strip() or email_addr,
                },
                "company_gmail",
            )
    return (
        {
            "smtp_host": "",
            "smtp_port": int(settings.smtp_port or 587),
            "smtp_user": "",
            "smtp_password": "",
            "smtp_from": (settings.smtp_from or "").strip(),
        },
        "none",
    )


@router.get("/smtp", response_model=SmtpConfigRead)
async def smtp_config(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: CurrentUser,
    company_id: CurrentCompanyId,
) -> SmtpConfigRead:
    if current_user.role != UserRole.owner:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Только владелец")
    creds, source = await _resolve_smtp(db, company_id)
    return SmtpConfigRead(
        host=str(creds.get("smtp_host") or ""),
        port=int(creds.get("smtp_port") or 587),
        user=str(creds.get("smtp_user") or ""),
        from_email=str(creds.get("smtp_from") or settings.smtp_from or ""),
        has_password=bool(str(creds.get("smtp_password") or "").strip()),
        public_app_url=settings.public_app_url,
        public_api_base_url=settings.public_api_base_url,
        source=source,
    )


@router.patch("/smtp", response_model=SmtpConfigRead)
async def patch_smtp_config(
    body: SmtpConfigPatch,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: CurrentUser,
    company_id: CurrentCompanyId,
) -> SmtpConfigRead:
    """Сохранить SMTP компании (Gmail app password) — письма приглашений без Amvera env."""
    if current_user.role != UserRole.owner:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Только владелец")
    if (settings.smtp_host or "").strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="SMTP задан в переменных Amvera (env). Меняйте там или очистите SMTP_HOST.",
        )

    user = body.user.strip().lower()
    if "@" not in user:
        raise HTTPException(status_code=400, detail="Укажите email SMTP (USER)")
    from_email = (body.from_email or user).strip() or user
    host = (body.host or "smtp.gmail.com").strip() or "smtp.gmail.com"
    port = int(body.port or 587)

    integ = await get_gmail_integration(db, company_id)
    new_password = (body.password or "").strip()
    if integ is None:
        if not new_password:
            raise HTTPException(
                status_code=400,
                detail="Укажите пароль приложения Gmail (PASSWORD)",
            )
        pipe = (
            await db.execute(
                select(Pipeline).where(Pipeline.company_id == company_id).order_by(Pipeline.id.asc()).limit(1)
            )
        ).scalars().first()
        if pipe is None:
            raise HTTPException(status_code=400, detail="Сначала создайте воронку в CRM")
        stage = (
            await db.execute(
                select(PipelineStage)
                .where(PipelineStage.pipeline_id == pipe.id)
                .order_by(PipelineStage.order.asc(), PipelineStage.id.asc())
                .limit(1)
            )
        ).scalars().first()
        if stage is None:
            raise HTTPException(status_code=400, detail="В воронке нет стадий")
        integ = Integration(
            company_id=company_id,
            name="Gmail SMTP",
            provider=IntegrationProvider.gmail,
            is_active=True,
            pipeline_id=int(pipe.id),
            stage_id=int(stage.id),
            secret=secrets.token_urlsafe(24),
            config={},
        )
        db.add(integ)
        await db.flush()
    else:
        old_cfg = integ.config if isinstance(integ.config, dict) else {}
        if not new_password:
            new_password = str(old_cfg.get("app_password") or old_cfg.get("appPassword") or "").strip()
        if not new_password:
            raise HTTPException(status_code=400, detail="Укажите пароль приложения Gmail (PASSWORD)")
        integ.is_active = True

    integ.config = {
        "email": user,
        "gmail_email": user,
        "app_password": new_password,
        "smtp_host": host,
        "smtp_port": port,
        "from_email": from_email,
        "imap_host": "imap.gmail.com",
    }
    flag_modified(integ, "config")
    await db.commit()
    creds, source = await _resolve_smtp(db, company_id)
    return SmtpConfigRead(
        host=str(creds.get("smtp_host") or ""),
        port=int(creds.get("smtp_port") or 587),
        user=str(creds.get("smtp_user") or ""),
        from_email=str(creds.get("smtp_from") or ""),
        has_password=bool(str(creds.get("smtp_password") or "").strip()),
        public_app_url=settings.public_app_url,
        public_api_base_url=settings.public_api_base_url,
        source=source,
    )


@router.post("/smtp/test")
async def smtp_test(
    body: SmtpTestBody,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: CurrentUser,
    company_id: CurrentCompanyId,
) -> dict:
    if current_user.role != UserRole.owner:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Только владелец")
    creds, _source = await _resolve_smtp(db, company_id)
    host = str(creds.get("smtp_host") or "").strip() or None
    user = str(creds.get("smtp_user") or "").strip() or None
    password = str(creds.get("smtp_password") or "").strip() or None
    from_addr = str(creds.get("smtp_from") or "").strip() or None
    ok, mail_err = send_email(
        body.to_email.strip(),
        "CRM-DN SMTP test",
        "Тестовое письмо CRM-DN: SMTP настроен корректно.",
        smtp_host=host,
        smtp_port=int(creds.get("smtp_port") or 587),
        smtp_user=user,
        smtp_password=password,
        smtp_from=from_addr,
    )
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=mail_err
            or "SMTP test failed. Check HOST/PORT/USER/PASSWORD/FROM.",
        )
    return {"ok": True}


class StudioEventBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    props: dict | None = None
    path: str | None = Field(default=None, max_length=500)
    lang: str | None = Field(default=None, max_length=16)


@router.post("/studio-event")
async def studio_event(body: StudioEventBody) -> dict:
    """Public fire-and-forget funnel events from the studio landing / demos."""
    # Keep light: structured log only — no auth, no persistence dependency.
    safe_name = body.name.strip()[:120]
    print(
        f"[studio-event] name={safe_name!r} path={body.path!r} lang={body.lang!r} props={body.props!r}",
        flush=True,
    )
    return {"ok": True}


@router.post("/demo-request")
async def demo_request(
    body: DemoRequestBody,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    phone_norm = "".join(ch for ch in body.phone if ch.isdigit())
    if len(phone_norm) < 7:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Некорректный телефон")
    try:
        await provision_demo_account(
            db,
            full_name=body.full_name.strip(),
            phone=body.phone.strip(),
            email=str(body.email).strip(),
            message=(body.message or "").strip() or None,
        )
        await db.commit()
    except ValueError as e:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    except RuntimeError as e:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    except Exception:
        await db.rollback()
        raise
    return {"ok": True, "message": "На указанный email отправлены логин и пароль для входа в демо."}


class BackgroundEventRead(BaseModel):
    ts: str
    source: str
    ok: bool
    message: str
    detail: str | None = None


@router.get("/background-events", response_model=list[BackgroundEventRead])
async def get_background_events(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: CurrentUser,
    limit: int = Query(50, ge=1, le=100),
) -> list[BackgroundEventRead]:
    await assert_owner_admin_or_chief_expert(db, current_user)
    raw = list_background_events(limit)
    return [BackgroundEventRead(**x) for x in raw]


class TariffStatusRead(BaseModel):
    max_active_users: int
    max_integrations: int
    platform_max_active_users: int
    platform_max_integrations: int
    company_override_active_users: int | None = None
    company_override_integrations: int | None = None
    tariff_plan_id: int | None = None
    tariff_plan_name: str | None = None
    active_users: int
    integrations: int


class TariffAccessRead(BaseModel):
    """Текущий тариф компании: разрешённые функции и подсказки по апгрейду."""

    plan_id: int | None = None
    plan_name: str | None = None
    enabled_features: list[str]
    feature_labels: dict[str, str]
    upgrade_hints: dict[str, list[str]]


@router.get("/tariff", response_model=TariffStatusRead)
async def get_tariff_status(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: CurrentUser,
    company_id: CurrentCompanyId,
) -> TariffStatusRead:
    await assert_owner_admin_or_chief_expert(db, current_user)
    from app.services.tariff_effective import effective_tariff_max_active_users, effective_tariff_max_integrations

    au = await count_company_active_users(db, company_id)
    ig = await count_company_integrations(db, company_id)
    eff_u = await effective_tariff_max_active_users(db, company_id)
    eff_i = await effective_tariff_max_integrations(db, company_id)
    comp = await db.get(Company, company_id)
    plan_name = None
    plan_id = None
    if comp and comp.tariff_plan_id:
        pl = await db.get(TariffPlan, comp.tariff_plan_id)
        if pl is not None:
            plan_id = pl.id
            plan_name = pl.name
    return TariffStatusRead(
        max_active_users=eff_u,
        max_integrations=eff_i,
        platform_max_active_users=int(settings.tariff_max_active_users),
        platform_max_integrations=int(settings.tariff_max_integrations),
        company_override_active_users=comp.tariff_max_active_users if comp else None,
        company_override_integrations=comp.tariff_max_integrations if comp else None,
        tariff_plan_id=plan_id,
        tariff_plan_name=plan_name,
        active_users=au,
        integrations=ig,
    )


@router.get("/tariff-access", response_model=TariffAccessRead)
async def get_tariff_access(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: CurrentUser,
    company_id: CurrentCompanyId,
) -> TariffAccessRead:
    from app.services.tariff_catalog import ALL_FEATURE_KEYS, FEATURE_LABELS
    from app.services.tariff_plan_access import enabled_features_for_company, plan_names_including_feature

    if current_user.role == UserRole.super_owner:
        all_keys = sorted(ALL_FEATURE_KEYS)
        return TariffAccessRead(
            plan_id=None,
            plan_name=None,
            enabled_features=all_keys,
            feature_labels=dict(FEATURE_LABELS),
            upgrade_hints={},
        )
    allowed = await enabled_features_for_company(db, company_id)
    comp = await db.get(Company, company_id)
    plan_id = None
    plan_name = None
    if comp and comp.tariff_plan_id:
        pl = await db.get(TariffPlan, comp.tariff_plan_id)
        if pl is not None:
            plan_id = pl.id
            plan_name = pl.name
    upgrade_hints: dict[str, list[str]] = {}
    for key in sorted(ALL_FEATURE_KEYS):
        if key not in allowed:
            upgrade_hints[key] = await plan_names_including_feature(db, key)
    return TariffAccessRead(
        plan_id=plan_id,
        plan_name=plan_name,
        enabled_features=sorted(allowed),
        feature_labels=dict(FEATURE_LABELS),
        upgrade_hints=upgrade_hints,
    )


class ArchiveEveningStatsRead(BaseModel):
    day: str | None = None
    assigned: int = 0
    managers: int = 0
    ran_today: bool = False
    has_run: bool = False


@router.get("/archive-evening-stats", response_model=ArchiveEveningStatsRead)
async def get_archive_evening_stats(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: CurrentUser,
    company_id: CurrentCompanyId,
) -> ArchiveEveningStatsRead:
    """Метка раздачи Архива — только owner/admin (менеджерам 403)."""
    if current_user.role not in (UserRole.owner, UserRole.admin, UserRole.super_owner):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Только администратор")
    from app.services.archive_evening_reactivate import get_latest_archive_evening_stats

    data = await get_latest_archive_evening_stats(db, company_id=company_id)
    return ArchiveEveningStatsRead(**data)

