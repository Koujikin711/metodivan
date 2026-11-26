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

export const AFTER_WON_SALE_STORAGE_KEY = "mo_after_won_sale";

export function afterWonHref(
  leadId: number,
  me?: {
    crm_mode?: string | null;
    desk_sales_enabled?: boolean | null;
    booking_enabled?: boolean | null;
  } | null,
): string {
  void me;
  // sale_form=1 stays until user hides/saves — survives remount
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

export function stashAfterWonSale(state: AfterWonNavState): void {
  try {
    sessionStorage.setItem(AFTER_WON_SALE_STORAGE_KEY, JSON.stringify(state));
  } catch {
    /* ignore */
  }
}

export function peekAfterWonSaleStash(): AfterWonNavState | null {
  try {
    const raw = sessionStorage.getItem(AFTER_WON_SALE_STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as AfterWonNavState;
    if (!parsed?.openSaleForm || !(parsed.leadId > 0)) return null;
    return parsed;
  } catch {
    return null;
  }
}

export function clearAfterWonSaleStash(): void {
  try {
    sessionStorage.removeItem(AFTER_WON_SALE_STORAGE_KEY);
  } catch {
    /* ignore */
  }
}

export function shouldOpenSaleFormFromUrl(search: string): boolean {
  try {
    const sp = new URLSearchParams(search.startsWith("?") ? search.slice(1) : search);
    return sp.get("sale_form") === "1" || Boolean(sp.get("lead_id"));
  } catch {
    return false;
  }
}
