import type { HTMLAttributes, ReactNode } from "react";

/**
 * CRM-DN: один teal-семейный ряд вместо радуги MetodiOne.
 * Logout/pink остаются приглушённым rose для «Выход».
 */
const variants = {
  crm: "bg-[#0f766e]",
  indigo: "bg-[#0d9488]",
  purple: "bg-[#0f766e]",
  finance: "bg-[#0f766e]",
  tariff: "bg-[#14b8a6]",
  online: "bg-[#115e59]",
  blue: "bg-[#0d9488]",
  analytics: "bg-[#0f766e]",
  tasks: "bg-[#134e4a]",
  chat: "bg-[#14b8a6]",
  teal: "bg-[#0d9488]",
  trainer: "bg-[#0f766e]",
  integrations: "bg-[#115e59]",
  platform: "bg-[#0d9488]",
  logout: "bg-[#be123c]",
  pink: "bg-[#be123c]",
} as const;

export type NavIconVariant = keyof typeof variants;

type Props = {
  children: ReactNode;
  variant?: NavIconVariant;
  className?: string;
} & HTMLAttributes<HTMLDivElement>;

const darkInk: Partial<Record<NavIconVariant, true>> = {
  tariff: true,
  chat: true,
};

export function NavIconBadge({ children, variant = "crm", className = "", ...rest }: Props) {
  return (
    <div
      {...rest}
      data-nav-variant={variant}
      className={[
        "shell-nav-icon flex shrink-0 items-center justify-center rounded-xl",
        darkInk[variant] ? "text-slate-900" : "text-white",
        variants[variant],
        className,
      ].join(" ")}
    >
      <span className="flex items-center justify-center [&_svg]:stroke-[1.5] [&_svg]:stroke-current [&_svg]:fill-none">
        {children}
      </span>
    </div>
  );
}
