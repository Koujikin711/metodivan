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

const HEALTH_POLL_MS = 2500;
/** После долгого outage — полный reload (подтянуть новый бандл после деплоя). */
const HARD_RELOAD_AFTER_MS = 45_000;
/** После успешного /health не реагируем на старые error в кэше — иначе мигание UI. */
const RECOVERY_GRACE_MS = 8_000;

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
  const graceUntilRef = useRef(0);
  const retryBusyRef = useRef(false);

  useEffect(() => {
    const scanQueries = () => {
      if (Date.now() < graceUntilRef.current) return;
      if (recoveringRef.current) return;
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

  const clearStaleOutageErrors = () => {
    queryClient
      .getQueryCache()
      .getAll()
      .forEach((q) => {
        if (q.state.status === "error" && isServerUnavailableError(q.state.error)) {
          q.reset();
        }
      });
  };

  const recoverFromOutage = async () => {
    if (recoveringRef.current) return false;
    recoveringRef.current = true;
    try {
      const res = await fetch("/health", {
        method: "GET",
        headers: { Accept: "application/json" },
        cache: "no-store",
      });
      if (!res.ok) return false;
      graceUntilRef.current = Date.now() + RECOVERY_GRACE_MS;
      clearStaleOutageErrors();
      setServerOutage(false);
      await queryClient.invalidateQueries();
      await queryClient.refetchQueries({ type: "active" });
      return true;
    } catch {
      return false;
    } finally {
      recoveringRef.current = false;
    }
  };

  useEffect(() => {
    if (!down) return;
    toast.dismiss();
    let cancelled = false;
    const startedAt = Date.now();

    const probe = async () => {
      if (cancelled || recoveringRef.current || retryBusyRef.current) return;
      const ok = await recoverFromOutage();
      if (!ok && Date.now() - startedAt >= HARD_RELOAD_AFTER_MS) {
        window.location.reload();
      }
    };

    void probe();
    const id = window.setInterval(() => void probe(), HEALTH_POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- recover closes over stable refs
  }, [down, queryClient]);

  if (!down) return <>{children}</>;

  return (
    <div className="server-down-gate flex min-h-0 flex-1 flex-col">
      <ServerUnavailableState
        message={message}
        onRetry={() => {
          if (retryBusyRef.current) return;
          retryBusyRef.current = true;
          void (async () => {
            try {
              const ok = await recoverFromOutage();
              if (!ok) {
                /* keep overlay — health ещё мёртв; не дёргаем refetch */
                toast.dismiss();
              }
            } finally {
              retryBusyRef.current = false;
            }
          })();
        }}
      />
    </div>
  );
}
