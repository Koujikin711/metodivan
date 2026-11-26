/**
 * После «Удачно» всегда KPI «Добавить продажу» (Пакеты / услуги).
 * CRM-DN clinic тоже: booking_enabled=true, но нужная форма — продажа, не онлайн-запись.
 */
export function afterWonPath(_me?: {
  crm_mode?: string | null;
  desk_sales_enabled?: boolean | null;
  booking_enabled?: boolean | null;
} | null): "/kpi" {
  return "/kpi";
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
  me?: {
    crm_mode?: string | null;
    desk_sales_enabled?: boolean | null;
    booking_enabled?: boolean | null;
  } | null,
): string {
  void me;
  // sale_form=1 → KPI сразу раскрывает форму, даже до fetch лида
  return `/kpi?tab=manual&sale_form=1&lead_id=${leadId}`;
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
