import { NavLink } from "react-router-dom";
import { useMemo } from "react";

import { GradientIconBox } from "@/components/GradientIconBox";
import { LogOut } from "@/components/icons";
import { ShellNavIcon } from "@/components/ShellNavLink";
import { appLexicon } from "@/lib/appLexicon";
import {
  buildShellSidebarNavItems,
  type ShellSidebarNavItem,
} from "@/lib/shellSidebarNavItems";

type Props = {
  scope: string;
  isSuperOwner: boolean;
  isManagerNav: boolean;
  isExpert: boolean;
  isChiefExpert: boolean;
  isAdministrator?: boolean;
  isCurator?: boolean;
  alsoCurator?: boolean;
  alsoExpert?: boolean;
  isAccountant?: boolean;
  isRop?: boolean;
  showFinance: boolean;
  showExpenses?: boolean;
  showMarketing?: boolean;
  showIntegrationsHub: boolean;
  showKpi: boolean;
  bookingEnabled?: boolean;
  deskSalesEnabled?: boolean;
  chatStagesEnabled?: boolean;
  showExtraServices?: boolean;
  showCuratorJournal?: boolean;
  hideEmployees?: boolean;
  showNavForFeature: (feature: string) => boolean;
  compact?: boolean;
  onLogout: () => void;
  /** Скрыть при открытом диалоге чата (клавиатура / полноэкран). */
  hidden?: boolean;
};

const MAX_ITEMS = 5;

function linkClass(isActive: boolean, compact: boolean) {
  return [
    "shell-mobile-nav-link group flex min-w-0 flex-col items-center justify-center text-center",
    compact
      ? "flex-1 gap-0.5 rounded-xl px-0.5 py-1"
      : "min-w-[4.1rem] shrink-0 gap-1 rounded-2xl px-1.5 py-1.5",
    isActive ? "is-active" : "",
  ]
    .filter(Boolean)
    .join(" ");
}

function pickMobileItems(items: ShellSidebarNavItem[]): ShellSidebarNavItem[] {
  if (items.length <= MAX_ITEMS) return items;
  const priority = [
    "chat",
    "desk",
    "crm",
    "my-leads",
    "booking",
    "sales",
    "kpi",
    "expenses",
    "rop",
    "tasks",
    "finance",
  ];
  const byId = new Map(items.map((i) => [i.id, i]));
  const picked: ShellSidebarNavItem[] = [];
  for (const id of priority) {
    const hit = byId.get(id);
    if (hit) {
      picked.push(hit);
      byId.delete(id);
    }
    if (picked.length >= MAX_ITEMS) return picked;
  }
  for (const item of items) {
    if (picked.length >= MAX_ITEMS) break;
    if (!picked.some((p) => p.id === item.id)) picked.push(item);
  }
  return picked;
}

export function ShellMobileBottomNav({
  scope,
  isSuperOwner,
  isManagerNav,
  isExpert,
  isChiefExpert,
  isAdministrator = false,
  isCurator = false,
  alsoCurator = false,
  alsoExpert = false,
  isAccountant = false,
  isRop = false,
  showFinance,
  showExpenses = false,
  showMarketing = false,
  showIntegrationsHub,
  showKpi,
  bookingEnabled = true,
  deskSalesEnabled = false,
  chatStagesEnabled = true,
  showExtraServices = false,
  showCuratorJournal = false,
  hideEmployees = false,
  showNavForFeature,
  compact = false,
  onLogout,
  hidden = false,
}: Props) {
  const items = useMemo(() => {
    void scope;
    const all = buildShellSidebarNavItems({
      isSuperOwner,
      isManagerNav,
      isExpert,
      isChiefExpert,
      isAdministrator,
      isCurator,
      alsoCurator,
      alsoExpert,
      isAccountant,
      isRop,
      showFinance,
      showExpenses,
      showMarketing,
      showIntegrationsHub,
      showKpi,
      bookingEnabled,
      deskSalesEnabled,
      chatStagesEnabled,
      showExtraServices,
      showCuratorJournal,
      hideEmployees,
      showNavForFeature,
      navLex: appLexicon,
    });
    return pickMobileItems(all);
  }, [
    scope,
    isSuperOwner,
    isManagerNav,
    isExpert,
    isChiefExpert,
    isAdministrator,
    isCurator,
    alsoCurator,
    alsoExpert,
    isAccountant,
    isRop,
    showFinance,
    showExpenses,
    showMarketing,
    showIntegrationsHub,
    showKpi,
    bookingEnabled,
    deskSalesEnabled,
    chatStagesEnabled,
    showExtraServices,
    showCuratorJournal,
    hideEmployees,
    showNavForFeature,
  ]);

  if (hidden) return null;

  const iconBox = compact ? "h-7 w-7 [&_svg]:h-3.5 [&_svg]:w-3.5" : "h-9 w-9 [&_svg]:h-4 [&_svg]:w-4";
  const labelClass = "max-w-full truncate text-[9px] leading-tight";

  return (
    <nav
      aria-label="Основная навигация"
      className={[
        "shell-mobile-bottom-nav print:hidden no-scrollbar fixed bottom-0 left-0 right-0 z-50 flex items-stretch border-t border-[var(--mo-border)] bg-[var(--mo-surface-elevated)]/95 backdrop-blur-xl lg:hidden",
        compact
          ? "gap-0 px-1 pt-1.5 pb-[max(0.35rem,env(safe-area-inset-bottom))]"
          : "touch-pan-x gap-0.5 overflow-x-auto overscroll-x-contain px-1 pt-2 pb-[max(0.5rem,env(safe-area-inset-bottom))]",
      ].join(" ")}
    >
      {items.map((item) => (
        <NavLink
          key={item.id}
          preventScrollReset
          to={item.to}
          end={item.end}
          title={item.title}
          className={({ isActive }) => linkClass(isActive, compact)}
        >
          <GradientIconBox variant={item.variant} className={iconBox}>
            <ShellNavIcon iconKey={item.iconKey} />
          </GradientIconBox>
          <span className={labelClass}>{item.labelShort}</span>
        </NavLink>
      ))}
      <button
        type="button"
        onClick={onLogout}
        title="Выход"
        className={[
          linkClass(false, compact),
          "lux-caption transition-[background-color,color] hover:bg-white/[0.04] hover:text-[var(--mo-text)]",
        ].join(" ")}
      >
        <GradientIconBox variant="pink" className={iconBox}>
          <LogOut className={compact ? "h-3.5 w-3.5" : "h-4 w-4"} />
        </GradientIconBox>
        <span className={labelClass}>Выход</span>
      </button>
    </nav>
  );
}
