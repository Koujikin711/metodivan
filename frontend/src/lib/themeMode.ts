export type ThemeMode = "light" | "dark";

const STORAGE_KEY = "mo-theme";

export function getStoredTheme(): ThemeMode {
  if (typeof window === "undefined") return "dark";
  const raw = localStorage.getItem(STORAGE_KEY);
  if (raw === "dark" || raw === "light") return raw;
  // CRM-DN: тёмный teal как на скринах канбана / РОП (не светлый MetodiOne).
  return "dark";
}

export function applyTheme(mode: ThemeMode) {
  if (typeof document === "undefined") return;
  document.documentElement.dataset.theme = mode;
  document.documentElement.style.colorScheme = mode;
  localStorage.setItem(STORAGE_KEY, mode);
}

function withThemeTransition(apply: () => void) {
  if (typeof document === "undefined") {
    apply();
    return;
  }
  const root = document.documentElement;
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  if (reduced) {
    apply();
    return;
  }

  /** Только CSS-токены: View Transition даёт мигание и «прыжок» снимков. */
  const DURATION_MS = 320;

  root.classList.add("theme-transitioning");
  apply();
  window.setTimeout(() => root.classList.remove("theme-transitioning"), DURATION_MS);
}

export function toggleTheme(): ThemeMode {
  const next: ThemeMode = getStoredTheme() === "dark" ? "light" : "dark";
  withThemeTransition(() => applyTheme(next));
  return next;
}

/** Вызывать до первого рендера React, чтобы не мигал светлый фон. */
export function initTheme() {
  // v3: CRM-DN = тёмный стиль канбана; сбрасываем прежний force-light (v2).
  if (typeof window !== "undefined" && !localStorage.getItem("mo-theme-crmdn-dark-v3")) {
    localStorage.setItem(STORAGE_KEY, "dark");
    localStorage.setItem("mo-theme-crmdn-dark-v3", "1");
  }
  applyTheme(getStoredTheme());
}
