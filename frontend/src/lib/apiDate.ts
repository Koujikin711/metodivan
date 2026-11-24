/** API отдаёт UTC datetime без суффикса Z — в браузере (Asia/Dushanbe) это
 *  ошибочно читается как локальное время (−5 ч). Наивный ISO трактуем как UTC. */

const HAS_ZONE = /([zZ]|[+-]\d{2}:?\d{2})$/;

export function parseApiDate(raw: string | null | undefined): Date | null {
  if (raw == null) return null;
  const s = String(raw).trim();
  if (!s) return null;
  const normalized = HAS_ZONE.test(s) ? s : s.includes("T") ? `${s}Z` : `${s}T00:00:00Z`;
  const d = new Date(normalized);
  return Number.isNaN(d.getTime()) ? null : d;
}

export const APP_TIME_ZONE =
  (import.meta.env.VITE_APP_TIMEZONE as string | undefined)?.trim() || "Asia/Dushanbe";
