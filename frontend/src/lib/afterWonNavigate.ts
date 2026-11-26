/** После стадии «Удачно»: sales → KPI «Добавить продажу», иначе онлайн-запись. */
export function afterWonPath(me: {
  crm_mode?: string | null;
  desk_sales_enabled?: boolean | null;
  booking_enabled?: boolean | null;
} | null | undefined): "/kpi" | "/booking" {
  const salesSpace = me?.crm_mode === "sales" || Boolean(me?.desk_sales_enabled);
  if (salesSpace || me?.booking_enabled === false) return "/kpi";
  return "/booking";
}

export function afterWonHref(
  leadId: number,
  me: {
    crm_mode?: string | null;
    desk_sales_enabled?: boolean | null;
    booking_enabled?: boolean | null;
  } | null | undefined,
): string {
  const path = afterWonPath(me);
  if (path === "/kpi") return `/kpi?tab=manual&lead_id=${leadId}`;
  return `${path}?lead_id=${leadId}`;
}
