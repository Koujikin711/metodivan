import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import toast from "react-hot-toast";

import metodiMarkUrl from "@/assets/metodione-mark.svg?url";

import App from "./App";
import "./index.css";
import "leaflet/dist/leaflet.css";
import { isServerUnavailableError } from "@/components/ServerUnavailableState";
import { isServerOutageActive, looksLikeServerOutageMessage } from "@/lib/serverOutage";
import { initTheme } from "@/lib/themeMode";

initTheme();

/** Во время 503/деплоя не сыпать одинаковые «Нет связи…» — есть один экран-робот. */
const rawToastError = toast.error.bind(toast);
toast.error = ((message: Parameters<typeof toast.error>[0], opts?: Parameters<typeof toast.error>[1]) => {
  if (isServerOutageActive()) return "" as ReturnType<typeof toast.error>;
  if (typeof message === "string" && looksLikeServerOutageMessage(message)) {
    return "" as ReturnType<typeof toast.error>;
  }
  if (message instanceof Error && isServerUnavailableError(message)) {
    return "" as ReturnType<typeof toast.error>;
  }
  return rawToastError(message, opts);
}) as typeof toast.error;

function applyMetodiFavicons() {
  for (const rel of ["icon", "apple-touch-icon"] as const) {
    let el = document.querySelector<HTMLLinkElement>(`link[rel="${rel}"]`);
    if (!el) {
      el = document.createElement("link");
      el.rel = rel;
      document.head.appendChild(el);
    }
    el.type = "image/svg+xml";
    el.href = metodiMarkUrl;
  }
}
applyMetodiFavicons();

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: (failureCount, error) => {
        if (isServerUnavailableError(error)) return failureCount < 4;
        return failureCount < 1;
      },
      retryDelay: (attempt) => Math.min(800 * 2 ** attempt, 6_000),
    },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
