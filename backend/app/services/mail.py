import logging
import smtplib
import ssl
from email.message import EmailMessage

from app.config import settings

logger = logging.getLogger(__name__)


def smtp_misconfig_hint(
    *,
    smtp_host: str | None = None,
    smtp_user: str | None = None,
    smtp_password: str | None = None,
) -> str | None:
    """Коротко: чего не хватает для почты (без секретов)."""
    host = (smtp_host if smtp_host is not None else settings.smtp_host) or ""
    user = (smtp_user if smtp_user is not None else settings.smtp_user) or ""
    password = (smtp_password if smtp_password is not None else settings.smtp_password) or ""
    if not host.strip():
        return (
            "SMTP_HOST пуст — сохраните Gmail или Mail.ru в Сотрудники → SMTP "
            "(или переменные Amvera / Интеграции)"
        )
    if not user.strip():
        return "SMTP_USER пуст"
    if not password.strip():
        return "SMTP_PASSWORD пуст (пароль приложения Gmail или пароль ящика Mail.ru)"
    return None


def send_email(
    to_email: str,
    subject: str,
    body: str,
    html_body: str | None = None,
    attachments: list[tuple[str, bytes]] | None = None,
    *,
    smtp_host: str | None = None,
    smtp_port: int | None = None,
    smtp_user: str | None = None,
    smtp_password: str | None = None,
    smtp_from: str | None = None,
) -> tuple[bool, str | None]:
    """Отправка письма. Возвращает (ok, error_detail).

    Опциональные smtp_* перекрывают env — для Gmail-интеграции компании.
    """
    host = (smtp_host if smtp_host is not None else settings.smtp_host) or ""
    user = (smtp_user if smtp_user is not None else settings.smtp_user) or ""
    password_raw = smtp_password if smtp_password is not None else settings.smtp_password
    hint = smtp_misconfig_hint(smtp_host=host, smtp_user=user, smtp_password=password_raw)
    if hint:
        logger.warning("%s — письмо не отправлено (%s)", hint, to_email)
        return False, hint
    to_addr = (to_email or "").strip()
    if not to_addr or "@" not in to_addr:
        return False, "Некорректный адрес получателя"
    msg = EmailMessage()
    msg["Subject"] = subject
    from_addr = (
        (smtp_from if smtp_from is not None else None)
        or (settings.smtp_from or "").strip()
        or (user or "").strip()
        or "no-reply@crm.local"
    )
    msg["From"] = from_addr.strip()
    msg["To"] = to_addr
    msg.set_content(body)
    if html_body:
        msg.add_alternative(html_body, subtype="html")
    for filename, payload in attachments or []:
        msg.add_attachment(payload, maintype="text", subtype="csv", filename=filename)
    user = user.strip()
    # Gmail App Password часто копируют с пробелами — они ломают login.
    password = "".join((password_raw or "").split())
    host = host.strip()
    port = int(smtp_port if smtp_port is not None else (settings.smtp_port or 587))
    try:
        if port == 465:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(host, port, timeout=25, context=context) as server:
                server.ehlo()
                if user:
                    server.login(user, password)
                server.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=25) as server:
                server.ehlo()
                if port in (587, 25):
                    server.starttls(context=ssl.create_default_context())
                    server.ehlo()
                if user:
                    server.login(user, password)
                server.send_message(msg)
        return True, None
    except Exception as e:
        err = str(e).strip() or type(e).__name__
        logger.exception(
            "SMTP send failed to=%s host=%s port=%s from=%s user=%s err=%s",
            to_addr,
            host,
            port,
            settings.smtp_from or settings.smtp_user,
            user or "—",
            e,
        )
        # Короткая подсказка без паролей
        low = err.lower()
        if "authentication" in low or "username and password" in low or "badcredentials" in low:
            return False, "SMTP: неверный логин/пароль (Gmail app password или пароль Mail.ru)"
        if "timed out" in low or "timeout" in low:
            return False, "SMTP: таймаут соединения с почтовым сервером"
        return False, f"SMTP: {err[:180]}"

