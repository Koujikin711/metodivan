/** После «Удачно»: sales → KPI «Добавить продажу», иначе онлайн-запись. */
export function afterWonPath(me: {
  crm_mode?: string | null;
  desk_sales_enabled?: boolean | null;
  booking_enabled?: boolean | null;
} | null | undefined): "/kpi" | "/booking" {
  // me ещё не загружен — на CRM-DN почти всегда продажа, не booking
  if (me == null) return "/kpi";
  const salesSpace = me.crm_mode === "sales" || Boolean(me.desk_sales_enabled);
  if (salesSpace || me.booking_enabled === false) return "/kpi";
  return "/booking";
}

export type AfterWonNavState = {
  openSaleForm: true;
  leadId: number;
  clientName?: string;
  clientPhone?: string;
  pipelineId?: number | null;
};

export function afterWonHref(
  leadId: number,
  me: {
    crm_mode?: string | null;
    desk_sales_enabled?: boolean | null;
    booking_enabled?: boolean | null;
  } | null | undefined,
): string {
  const path = afterWonPath(me);
  if (path === "/kpi") {
    // sale_form=1 → KPI сразу раскрывает форму, даже до fetch лида
    return `/kpi?tab=manual&sale_form=1&lead_id=${leadId}`;
  }
  return `${path}?lead_id=${leadId}`;
}

export function afterWonNavState(lead: {
  id: number;
  name?: string | null;
  phone?: string | null;
  phone_display?: string | null;
  pipeline_id?: number | null;
}): AfterWonNavState {
  const phone = (lead.phone_display || lead.phone || "").trim();
  return {
    openSaleForm: true,
    leadId: lead.id,
    clientName: (lead.name || "").trim() || undefined,
    clientPhone: phone || undefined,
    pipelineId: lead.pipeline_id ?? null,
  };
}
