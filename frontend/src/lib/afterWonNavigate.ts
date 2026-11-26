/** После стадии «Удачно»: sales-пространство → окно продаж, иначе онлайн-запись. */
export function afterWonPath(me: {
  crm_mode?: string | null;
  desk_sales_enabled?: boolean | null;
  booking_enabled?: boolean | null;
} | null | undefined): "/sales" | "/booking" {
  const salesSpace = me?.crm_mode === "sales" || Boolean(me?.desk_sales_enabled);
  if (salesSpace || me?.booking_enabled === false) return "/sales";
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
  return `${afterWonPath(me)}?lead_id=${leadId}`;
}
