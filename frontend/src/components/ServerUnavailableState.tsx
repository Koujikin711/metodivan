import { useState } from "react";

import robotGif from "@/assets/server-unavailable-robot.gif";

type Props = {
  message?: string;
  onRetry?: () => void;
};

/** Inline SVG — всегда виден, даже если сервер не отдаёт GIF (как раз на 503). */
function ServerDownRobotFallback() {
  return (
    <svg
      className="server-down__robot server-down__robot--svg"
      viewBox="0 0 200 190"
      width={280}
      height={266}
      aria-hidden
    >
      <defs>
        <linearGradient id="sd-body" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor="#14b8a6" />
          <stop offset="100%" stopColor="#0f766e" />
        </linearGradient>
        <linearGradient id="sd-face" x1="0%" y1="0%" x2="0%" y2="100%">
          <stop offset="0%" stopColor="#ecfdf5" />
          <stop offset="100%" stopColor="#99f6e4" />
        </linearGradient>
      </defs>
      {/* antenna */}
      <line x1="100" y1="18" x2="100" y2="38" stroke="#5eead4" strokeWidth="4" strokeLinecap="round" />
      <circle cx="100" cy="14" r="7" fill="#2dd4bf" />
      {/* head */}
      <rect x="48" y="38" width="104" height="78" rx="22" fill="url(#sd-body)" />
      <rect x="58" y="50" width="84" height="48" rx="14" fill="url(#sd-face)" />
      {/* eyes */}
      <circle className="server-down__eye" cx="82" cy="72" r="8" fill="#0f766e" />
      <circle cx="118" cy="72" r="8" fill="#0f766e" />
      {/* mouth */}
      <path d="M78 90 Q100 102 122 90" fill="none" stroke="#0f766e" strokeWidth="3.5" strokeLinecap="round" />
      {/* body */}
      <rect x="62" y="122" width="76" height="52" rx="16" fill="url(#sd-body)" />
      <rect x="78" y="136" width="44" height="18" rx="6" fill="#99f6e4" opacity="0.85" />
      {/* arms */}
      <rect x="28" y="128" width="28" height="12" rx="6" fill="#0d9488" />
      <rect x="144" y="128" width="28" height="12" rx="6" fill="#0d9488" />
      {/* feet */}
      <rect x="72" y="174" width="22" height="10" rx="5" fill="#134e4a" />
      <rect x="106" y="174" width="22" height="10" rx="5" fill="#134e4a" />
    </svg>
  );
}

/**
 * 503-экран: GIF из бандла (не зависит от /server-unavailable-robot.gif на мёртвом API),
 * при ошибке загрузки — SVG-робот, чтобы значок никогда не был «пустым».
 */
export function ServerUnavailableState({ message, onRetry }: Props) {
  const retry = onRetry ?? (() => window.location.reload());
  const label = onRetry ? "Повторить" : "Обновить страницу";
  const [gifFailed, setGifFailed] = useState(false);
  const [gifReady, setGifReady] = useState(false);

  return (
    <div
      className="server-down relative mx-auto flex w-full max-w-xl flex-col items-center overflow-hidden px-5 text-center"
      role="alert"
      aria-live="polite"
    >
      <div className="server-down__glow" aria-hidden />
      <div className="server-down__figure">
        {!gifReady || gifFailed ? <ServerDownRobotFallback /> : null}
        {!gifFailed ? (
          <img
            src={robotGif}
            alt=""
            width={493}
            height={467}
            className="server-down__robot"
            style={gifReady ? undefined : { position: "absolute", opacity: 0, pointerEvents: "none" }}
            draggable={false}
            onLoad={() => setGifReady(true)}
            onError={() => setGifFailed(true)}
          />
        ) : null}
      </div>
      <p className="server-down__eyebrow">503 · нет связи</p>
      <h2 className="server-down__title">Сервер не работает!</h2>
      <p className="server-down__text">
        {message?.trim() ||
          "Сервер временно недоступен (503). Подождите несколько секунд и повторите."}
      </p>
      <button type="button" className="btn-primary server-down__cta" onClick={retry}>
        {label}
      </button>
    </div>
  );
}

export function isServerUnavailableError(error: unknown): boolean {
  const msg = error instanceof Error ? error.message : String(error ?? "");
  return (
    /временно недоступен\s*\(50[234]\)/i.test(msg) ||
    /Сервер не работает/i.test(msg) ||
    /Сервер не ответил за \d+ с/i.test(msg) ||
    /Нет связи с (сервером|API)/i.test(msg)
  );
}
