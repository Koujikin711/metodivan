"""Исходящие сообщения в Green API (текст и файлы)."""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib import error as urlerror
from urllib import request as urlrequest

import httpx

from app.config import settings
from app.services.green_api_settings import green_api_base_from_config, green_media_base_from_config

logger = logging.getLogger(__name__)


def _via_qr_file(
    chat_id: str,
    file_bytes: bytes,
    filename: str,
    caption: str,
    mime: str | None,
    green_error: str,
) -> tuple[bool, str | None, str | None]:
    from app.services.wa_bridge_client import send_file

    ok, err, mid, down = send_file(chat_id, file_bytes, filename, caption, mime=mime)
    if ok:
        return True, None, mid
    if not down:
        return False, err or green_error or "Не удалось отправить файл", None
    return False, green_error or err or "WhatsApp QR не подключён и Green media недоступен", None


def _via_qr_text(chat_id: str, text: str, green_error: str) -> tuple[bool, str | None, str | None]:
    """Если Green API молчит, то же сообщение уходит через QR-сессию Baileys."""
    from app.services.wa_bridge_client import send_text

    ok, err, mid, down = send_text(chat_id, text)
    if ok:
        return True, None, mid
    if not down:
        return False, err or green_error, None
    return False, green_error, None


def send_green_text(config: dict | None, chat_id: str, text: str) -> tuple[bool, str | None, str | None]:
    cfg = config or {}
    instance_id = cfg.get("instance_id") or cfg.get("instanceId")
    api_token = cfg.get("api_token") or cfg.get("apiToken") or cfg.get("apiTokenInstance")
    if not instance_id or not api_token:
        return _via_qr_text(chat_id, text, "Нет instance_id/api_token в интеграции")
    base = green_api_base_from_config(cfg)
    url = f"{base}/waInstance{instance_id}/sendMessage/{api_token}"
    body = json.dumps({"chatId": chat_id, "message": text}).encode("utf-8")
    req = urlrequest.Request(url, data=body, method="POST", headers={"Content-Type": "application/json"})
    try:
        with urlrequest.urlopen(req, timeout=40) as resp:
            raw = resp.read().decode("utf-8") if resp else ""
            data = json.loads(raw) if raw else {}
            return True, None, str(data.get("idMessage") or "")
    except urlerror.HTTPError as e:
        return _via_qr_text(chat_id, text, f"GREEN API HTTP {e.code}")
    except Exception as e:
        return _via_qr_text(chat_id, text, str(e))


async def send_green_text_async(config: dict | None, chat_id: str, text: str) -> tuple[bool, str | None, str | None]:
    cfg = config or {}
    instance_id = cfg.get("instance_id") or cfg.get("instanceId")
    api_token = cfg.get("api_token") or cfg.get("apiToken") or cfg.get("apiTokenInstance")
    if not instance_id or not api_token:
        return _via_qr_text(chat_id, text, "Нет instance_id/api_token в интеграции")
    base = green_api_base_from_config(cfg)
    url = f"{base}/waInstance{instance_id}/sendMessage/{api_token}"
    body: dict[str, Any] = {"chatId": chat_id, "message": text}
    try:
        async with httpx.AsyncClient(timeout=40.0) as client:
            r = await client.post(url, json=body)
            if r.status_code >= 400:
                return _via_qr_text(chat_id, text, f"GREEN API HTTP {r.status_code}")
            data = r.json() if r.text else {}
            return True, None, str(data.get("idMessage") or "")
    except Exception as e:
        return _via_qr_text(chat_id, text, str(e))


async def send_green_location(
    config: dict | None,
    chat_id: str,
    *,
    latitude: float,
    longitude: float,
    name_location: str = "",
    address: str = "",
) -> tuple[bool, str | None, str | None]:
    """WhatsApp-пин через Green API sendLocation."""
    cfg = config or {}
    instance_id = cfg.get("instance_id") or cfg.get("instanceId")
    api_token = cfg.get("api_token") or cfg.get("apiToken") or cfg.get("apiTokenInstance")
    if not instance_id or not api_token:
        # QR-мост локацию не шлёт — уходит ссылка на карты текстом.
        label = (name_location or address or "Локация").strip() or "Локация"
        maps = f"https://maps.google.com/?q={latitude},{longitude}"
        return await send_green_text_async(cfg, chat_id, f"📍 {label}\n{maps}")
    base = green_api_base_from_config(cfg)
    url = f"{base}/waInstance{instance_id}/sendLocation/{api_token}"
    body: dict[str, Any] = {
        "chatId": chat_id,
        "latitude": float(latitude),
        "longitude": float(longitude),
    }
    if name_location.strip():
        body["nameLocation"] = name_location.strip()[:200]
    if address.strip():
        body["address"] = address.strip()[:500]
    try:
        async with httpx.AsyncClient(timeout=40.0) as client:
            r = await client.post(url, json=body)
            if r.status_code >= 400:
                label = (name_location or address or "Локация").strip() or "Локация"
                maps = f"https://maps.google.com/?q={latitude},{longitude}"
                return await send_green_text_async(
                    cfg,
                    chat_id,
                    f"📍 {label}\n{maps}",
                )
            data = r.json() if r.text else {}
            return True, None, str(data.get("idMessage") or "")
    except Exception:
        label = (name_location or address or "Локация").strip() or "Локация"
        maps = f"https://maps.google.com/?q={latitude},{longitude}"
        return await send_green_text_async(cfg, chat_id, f"📍 {label}\n{maps}")


def _upload_mime(filename: str, mime: str | None) -> str | None:
    low = (filename or "").lower()
    if low.endswith(".ogg") or low.endswith(".opus"):
        return "audio/ogg"
    if mime:
        return mime.split(";")[0].strip() or None
    return None


def _is_voice_file(filename: str, mime: str | None) -> bool:
    upload_mime = _upload_mime(filename, mime) or ""
    low_name = (filename or "").lower()
    return upload_mime.startswith("audio/") and (
        low_name.startswith("voice") or low_name.endswith((".ogg", ".opus", ".webm", ".m4a"))
    )


def _media_host_candidates(cfg: dict, instance_id: str) -> list[str]:
    hosts: list[str] = []
    primary = green_media_base_from_config(cfg)
    if primary:
        hosts.append(primary.rstrip("/"))
    # Часто в кабинете: https://7107.media.green-api.com для instance 7107…
    iid = str(instance_id or "").strip()
    if len(iid) >= 4 and iid[:4].isdigit():
        hosts.append(f"https://{iid[:4]}.media.green-api.com")
    hosts.append("https://media.green-api.com")
    hosts.append("https://media.greenapi.com")
    out: list[str] = []
    seen: set[str] = set()
    for h in hosts:
        key = h.rstrip("/").lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(h.rstrip("/"))
    return out


def _exc_detail(exc: BaseException) -> str:
    name = type(exc).__name__
    msg = str(exc).strip()
    if msg:
        return f"{name}: {msg}"[:400]
    return name


async def _send_file_by_url(
    *,
    cfg: dict,
    instance_id: str,
    api_token: str,
    chat_id: str,
    url_file: str,
    filename: str,
    caption: str,
    is_voice: bool,
) -> tuple[bool, str | None, str | None]:
    """sendFileByUrl на apiUrl — работает, когда media-хост с Amvera недоступен."""
    base = green_api_base_from_config(cfg)
    url = f"{base}/waInstance{instance_id}/sendFileByUrl/{api_token}"
    body: dict[str, Any] = {
        "chatId": chat_id,
        "urlFile": url_file,
        "fileName": filename or "file.ogg",
    }
    if caption:
        body["caption"] = caption
    if is_voice:
        body["typingType"] = "recording"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(25.0, connect=8.0)) as client:
            r = await client.post(url, json=body)
            if r.status_code >= 400:
                return False, (r.text or f"GREEN sendFileByUrl HTTP {r.status_code}")[:400], None
            data = r.json() if r.text else {}
            return True, None, str(data.get("idMessage") or "")
    except Exception as e:
        return False, _exc_detail(e), None


async def send_green_file_upload(
    config: dict | None,
    chat_id: str,
    file_bytes: bytes,
    filename: str,
    caption: str,
    mime: str | None = None,
    *,
    public_base_url: str | None = None,
) -> tuple[bool, str | None, str | None]:
    cfg = config or {}
    instance_id = cfg.get("instance_id") or cfg.get("instanceId")
    api_token = cfg.get("api_token") or cfg.get("apiToken") or cfg.get("apiTokenInstance")
    upload_name = filename or "file"
    upload_mime = _upload_mime(upload_name, mime)
    is_voice = _is_voice_file(upload_name, mime)
    if not instance_id or not api_token:
        return _via_qr_file(
            chat_id, file_bytes, upload_name, caption, upload_mime, "Нет instance_id/api_token в интеграции"
        )

    # 1) sendFileByUpload на media-хосты (короткий таймаут — Amvera gateway ~50s).
    # Несколько хостов подряд съедают бюджет шлюза — пробуем 1–2, затем URL.
    green_timeout = httpx.Timeout(10.0, connect=4.0)
    green_error = "Green media недоступен"
    file_part = (upload_name, file_bytes, upload_mime) if upload_mime else (upload_name, file_bytes)
    data: dict[str, Any] = {"chatId": chat_id, "fileName": upload_name}
    if caption:
        data["caption"] = caption
    if is_voice:
        data["typingType"] = "recording"

    hosts = _media_host_candidates(cfg, str(instance_id))[:2]
    for media_base in hosts:
        url = f"{media_base}/waInstance{instance_id}/sendFileByUpload/{api_token}"
        try:
            async with httpx.AsyncClient(timeout=green_timeout) as client:
                r = await client.post(url, data=data, files={"file": file_part})
                if r.status_code >= 400:
                    green_error = (r.text or f"GREEN media HTTP {r.status_code} @ {media_base}")[:400]
                    logger.warning("green sendFileByUpload fail host=%s status=%s", media_base, r.status_code)
                    continue
                j = r.json() if r.text else {}
                return True, None, str(j.get("idMessage") or "")
        except Exception as e:
            green_error = f"{_exc_detail(e)} @ {media_base}"
            logger.warning("green sendFileByUpload error host=%s detail=%s", media_base, green_error)

    # 2) sendFileByUrl через публичный URL CRM (api.green-api.com уже отвечает на текст).
    pub = (public_base_url or settings.public_api_base_url or "").strip().rstrip("/")
    if pub:
        from app.services.chat_media_store import save_temp_outbound_media

        saved = save_temp_outbound_media(file_bytes, upload_name, mime=upload_mime)
        if saved:
            token, safe_name = saved
            url_file = f"{pub}/api/chat/outbound-temp/{token}"
            ok, err, mid = await _send_file_by_url(
                cfg=cfg,
                instance_id=str(instance_id),
                api_token=str(api_token),
                chat_id=chat_id,
                url_file=url_file,
                filename=safe_name if is_voice else upload_name,
                caption=caption,
                is_voice=is_voice,
            )
            if ok:
                return True, None, mid
            green_error = err or green_error
            logger.warning("green sendFileByUrl fail detail=%s", green_error)
        else:
            green_error = f"{green_error}; не удалось сохранить временный файл для URL"

    # 3) QR / Baileys bridge
    return _via_qr_file(chat_id, file_bytes, upload_name, caption, upload_mime, green_error)
