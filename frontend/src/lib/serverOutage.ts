/** Глобальный флаг: API недоступен (деплой / 502–504 / сеть). UI показывает один экран, без спама тостов. */

type Listener = (down: boolean) => void;

let down = false;
let lastMessage = "";
const listeners = new Set<Listener>();

export function isServerOutageActive(): boolean {
  return down;
}

export function getServerOutageMessage(): string {
  return lastMessage;
}

export function setServerOutage(active: boolean, message = ""): void {
  const nextMsg = active ? message.trim() : "";
  if (down === active && lastMessage === nextMsg) return;
  down = active;
  lastMessage = nextMsg;
  for (const fn of listeners) {
    try {
      fn(down);
    } catch {
      /* ignore listener errors */
    }
  }
}

export function subscribeServerOutage(fn: Listener): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

/** Сообщение похоже на сетевой / gateway outage (для toast filter). */
export function looksLikeServerOutageMessage(message: unknown): boolean {
  const msg = typeof message === "string" ? message : message instanceof Error ? message.message : String(message ?? "");
  return (
    /временно недоступен\s*\(50[234]\)/i.test(msg) ||
    /Сервер не работает/i.test(msg) ||
    /Сервер не ответил за \d+ с/i.test(msg) ||
    /Нет связи с (сервером|API)/i.test(msg) ||
    /Failed to fetch/i.test(msg) ||
    /NetworkError|Load failed|network error/i.test(msg) ||
    /прокси \/api недоступен/i.test(msg)
  );
}
