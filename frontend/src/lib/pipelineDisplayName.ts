/** CRM-DN: в UI воронка — «Маркетинг» (вместо Sales funnel / Основная / пустого). */
const LEGACY = new Set([
  "sales funnel",
  "sales_funnel",
  "salesfunnel",
  "основная",
  "основной",
  "main",
  "default",
  "маркетинг",
]);

export function pipelineDisplayName(name: string | null | undefined): string {
  const raw = (name || "").trim();
  if (!raw) return "Маркетинг";
  const key = raw.toLowerCase().replace(/ё/g, "е");
  if (LEGACY.has(key)) return "Маркетинг";
  if (key.includes("sales") && key.includes("funnel")) return "Маркетинг";
  // Одна продуктовая воронка CRM-DN — не оставляем англ. ярлыки.
  if (/^[a-z0-9 _-]+$/i.test(raw) && /funnel|sales|pipeline/i.test(raw)) return "Маркетинг";
  return raw;
}
