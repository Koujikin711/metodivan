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


def _combine_send_errors(*parts: str | None) -> str:
    cleaned = [p.strip() for p in parts if isinstance(p, str) and p.strip()]
    if not cleaned:
        return "Не удалось отправить в WhatsApp (Green API и QR-мост недоступны)"
    # Unique, preserve order
    out: list[str] = []
    seen: set[str] = set()
    for p in cleaned:
        if p in seen:
            continue
        seen.add(p)
        out.append(p)
    return " · ".join(out)[:500]


def _green_http_error_detail(status_code: int, body: str | None) -> str:
    raw = (body or "").strip()
    if not raw:
        return f"GREEN API HTTP {status_code}"
    try:
        data = json.loads(raw)
    except Exception:
        return f"GREEN API HTTP {status_code}: {raw[:240]}"
    if isinstance(data, dict):
        for key in ("message", "error", "detail", "description"):
            val = data.get(key)
            if isinstance(val, str) and val.strip():
                return f"GREEN API HTTP {status_code}: {val.strip()[:240]}"
        if data.get("statusCode") and not data.get("idMessage"):
            return f"GREEN API HTTP {status_code}: {raw[:240]}"
    return f"GREEN API HTTP {status_code}: {raw[:240]}"


def _green_success_or_error(data: Any, raw: str) -> tuple[bool, str | None, str | None]:
    """Green иногда отвечает 200 без idMessage (ошибка в теле)."""
    if not isinstance(data, dict):
        if (raw or "").strip():
            return False, f"GREEN API: неожиданный ответ {raw[:240]}", None
        return False, "GREEN API: пустой ответ", None
    mid = str(data.get("idMessage") or "").strip()
    if mid:
        return True, None, mid
    for key in ("message", "error", "detail", "description"):
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            return False, f"GREEN API: {val.strip()[:240]}", None
    return False, "GREEN API: нет idMessage в ответе", None


def _via_qr_text(chat_id: str, text: str, green_error: str) -> tuple[bool, str | None, str | None]:
    """Если Green API молчит, то же сообщение уходит через QR-сессию Baileys."""
    from app.services.wa_bridge_client import send_text

    ok, err, mid, down = send_text(chat_id, text)
    if ok:
        return True, None, mid
    if down:
        qr_hint = err or "WhatsApp QR не подключён"
        return False, _combine_send_errors(green_error, qr_hint), None
    return False, _combine_send_errors(err, green_error), None


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
        with urlrequest.urlopen(req, timeout=25) as resp:
            raw = resp.read().decode("utf-8") if resp else ""
            data = json.loads(raw) if raw else {}
            ok, err, mid = _green_success_or_error(data, raw)
            if ok:
                return True, None, mid
            return _via_qr_text(chat_id, text, err or "GREEN API: нет idMessage")
    except urlerror.HTTPError as e:
        chunk = e.read().decode("utf-8", errors="replace")[:400]
        return _via_qr_text(chat_id, text, _green_http_error_detail(e.code, chunk))
    except Exception as e:
        return _via_qr_text(chat_id, text, _exc_detail(e))


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
        # Короче 40с: иначе кнопка «висит», а при таймауте str(ReadTimeout) бывает пустым.
        async with httpx.AsyncClient(timeout=httpx.Timeout(25.0, connect=8.0)) as client:
            r = await client.post(url, json=body)
            if r.status_code >= 400:
                return _via_qr_text(chat_id, text, _green_http_error_detail(r.status_code, r.text))
            data = r.json() if r.text else {}
            ok, err, mid = _green_success_or_error(data, r.text or "")
            if ok:
                return True, None, mid
            return _via_qr_text(chat_id, text, err or "GREEN API: нет idMessage")
    except Exception as e:
        detail = _exc_detail(e)
        logger.warning("sendMessage failed chat=%s detail=%s", chat_id, detail)
        return _via_qr_text(chat_id, text, detail)


def _location_text(latitude: float, longitude: float, name_location: str, address: str) -> str:
    label = (name_location or address or "Локация").strip() or "Локация"
    maps = f"https://maps.google.com/?q={latitude},{longitude}"
    if address.strip() and address.strip() not in label:
        return f"📍 {label}\n{address.strip()}\n{maps}"
    return f"📍 {label}\n{maps}"


async def send_green_location(
    config: dict | None,
    chat_id: str,
    *,
    latitude: float,
    longitude: float,
    name_location: str = "",
    address: str = "",
) -> tuple[bool, str | None, str | None]:
    """WhatsApp-пин через Green API sendLocation; при сбое — ссылка на карты текстом."""
    cfg = config or {}
    text = _location_text(latitude, longitude, name_location, address)
    instance_id = cfg.get("instance_id") or cfg.get("instanceId")
    api_token = cfg.get("api_token") or cfg.get("apiToken") or cfg.get("apiTokenInstance")
    if not instance_id or not api_token:
        # QR-мост локацию-пин не шлёт — только текст со ссылкой.
        return await send_green_text_async(cfg, chat_id, text)
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
    # Короткий таймаут: иначе кнопка «висит» 40с и кажется мёртвой.
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            r = await client.post(url, json=body)
            if r.status_code < 400:
                data = r.json() if r.text else {}
                return True, None, str(data.get("idMessage") or "")
            logger.warning(
                "sendLocation HTTP %s → fallback text chat=%s body=%s",
                r.status_code,
                chat_id,
                (r.text or "")[:240],
            )
    except Exception as e:
        logger.warning("sendLocation failed → fallback text chat=%s err=%s", chat_id, e)
    return await send_green_text_async(cfg, chat_id, text)

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
