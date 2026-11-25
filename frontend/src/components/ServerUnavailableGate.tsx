import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState, type ReactNode } from "react";
import toast from "react-hot-toast";
import { useLocation } from "react-router-dom";

import {
  ServerUnavailableState,
  isServerUnavailableError,
} from "@/components/ServerUnavailableState";
import {
  getServerOutageMessage,
  isServerOutageActive,
  setServerOutage,
  subscribeServerOutage,
} from "@/lib/serverOutage";

const HEALTH_POLL_MS = 2000;
/** После долгого outage — полный reload (подтянуть новый бандл после деплоя). */
const HARD_RELOAD_AFTER_MS = 45_000;

/**
 * Если API лёг (деплой Amvera / 502–504 / сеть) — один экран с роботом,
 * без пачки тостов; сами тихо опрашиваем /health и поднимаем UI когда сервер ожил.
 */
export function ServerUnavailableGate({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const location = useLocation();
  const [down, setDown] = useState(() => isServerOutageActive());
  const [message, setMessage] = useState<string | undefined>(() => getServerOutageMessage() || undefined);
  const recoveringRef = useRef(false);

  useEffect(() => {
    const scanQueries = () => {
      const queries = queryClient.getQueryCache().getAll();
      for (const q of queries) {
        if (q.state.status !== "error" || q.getObserversCount() === 0) continue;
        if (!isServerUnavailableError(q.state.error)) continue;
        const msg = q.state.error instanceof Error ? q.state.error.message : String(q.state.error);
        setServerOutage(true, msg);
        return;
      }
    };
    scanQueries();
    return queryClient.getQueryCache().subscribe(() => scanQueries());
  }, [queryClient, location.pathname]);

  useEffect(() => {
    return subscribeServerOutage((active) => {
      setDown(active);
      setMessage(active ? getServerOutageMessage() || undefined : undefined);
      if (active) {
        toast.dismiss();
      }
    });
  }, []);

  useEffect(() => {
    if (!down) return;
    toast.dismiss();
    let cancelled = false;
    const startedAt = Date.now();

    const probe = async () => {
      if (cancelled || recoveringRef.current) return;
      try {
        const res = await fetch("/health", {
          method: "GET",
          headers: { Accept: "application/json" },
          cache: "no-store",
        });
        if (!res.ok) {
          if (Date.now() - startedAt >= HARD_RELOAD_AFTER_MS) {
            window.location.reload();
          }
          return;
        }
        recoveringRef.current = true;
        setServerOutage(false);
        await queryClient.invalidateQueries();
        await queryClient.refetchQueries({ type: "active" });
      } catch {
        if (Date.now() - startedAt >= HARD_RELOAD_AFTER_MS) {
          window.location.reload();
        }
      } finally {
        recoveringRef.current = false;
      }
    };

    void probe();
    const id = window.setInterval(() => void probe(), HEALTH_POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [down, queryClient]);

  if (!down) return <>{children}</>;

  return (
    <div className="server-down-gate flex min-h-0 flex-1 flex-col">
      <ServerUnavailableState
        message={message}
        onRetry={() => {
          void (async () => {
            try {
              const res = await fetch("/health", {
                method: "GET",
                headers: { Accept: "application/json" },
                cache: "no-store",
              });
              if (res.ok) setServerOutage(false);
            } catch {
              /* keep overlay */
            }
            void queryClient.invalidateQueries();
            void queryClient.refetchQueries({ type: "active" });
          })();
        }}
      />
    </div>
  );
}
