/** Латинские дубли каналов (выкл.) — не показываем рядом с кириллическими активными. */
const HIDDEN_LATIN_SOURCE_NAMES = new Set(
  [
    "INSTAGRAM",
    "INSTAGRAM_DM",
    "INSTAGRAM MC",
    "Instagram",
    "WHATSAPP",
    "WhatsApp",
    "TELEGRAM",
    "Telegram",
  ].map((n) => n.trim().toLowerCase()),
);

export function isHiddenLeadSourceDuplicate(name: string, isActive: boolean): boolean {
  const key = (name || "").trim().toLowerCase();
  if (!key) return true;
  // Выключенные латинские дубли Instagram/Telegram/WhatsApp
  if (!isActive && HIDDEN_LATIN_SOURCE_NAMES.has(key)) return true;
  // На всякий случай не светим и активные латинские дубли, если есть кириллица
  if (HIDDEN_LATIN_SOURCE_NAMES.has(key)) return true;
  return false;
}

export function filterLeadSourcesForUi<T extends { name: string; is_active: boolean }>(
  sources: T[],
): T[] {
  return sources.filter((s) => !isHiddenLeadSourceDuplicate(s.name, s.is_active));
}
