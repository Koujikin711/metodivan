"""Фиксированная локация для исходящих WhatsApp-пинов (CRM-DN)."""

from __future__ import annotations

from app.config import settings


def clinic_whatsapp_location() -> dict[str, str | float]:
    raw = (settings.clinic_wa_location_name or "").strip()
    # CRM-DN: не оставляем Metodi Clinic из старого env на Amvera.
    if not raw or "metodi" in raw.lower():
        raw = "Маркетинг"
    return {
        "name_location": raw,
        "address": (settings.clinic_wa_location_address or "").strip(),
        "latitude": float(settings.clinic_wa_location_lat),
        "longitude": float(settings.clinic_wa_location_lng),
    }
