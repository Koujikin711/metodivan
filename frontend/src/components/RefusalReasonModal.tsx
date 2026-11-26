import { useEffect, useRef, useState } from "react";

type Props = {
  open: boolean;
  title?: string;
  hint?: string;
  confirmLabel?: string;
  busy?: boolean;
  onClose: () => void;
  onConfirm: (reason: string) => void;
};

/** Модал причины отказа — вместо window.prompt (ломается в Safari/Chrome на телефоне). */
export function RefusalReasonModal({
  open,
  title = "Причина отказа",
  hint = "Обязательно укажите, почему клиент отказался — это видно в карточке и отчётах.",
  confirmLabel = "Поставить отказ",
  busy = false,
  onClose,
  onConfirm,
}: Props) {
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (!open) return;
    setReason("");
    setError("");
    const t = window.setTimeout(() => textareaRef.current?.focus(), 50);
    return () => window.clearTimeout(t);
  }, [open]);

  if (!open) return null;

  function submit() {
    const trimmed = reason.trim();
    if (!trimmed) {
      setError("Укажите причину отказа");
      textareaRef.current?.focus();
      return;
    }
    onConfirm(trimmed);
  }

  return (
    <div
      className="fixed inset-0 z-[90] flex items-end justify-center bg-black/45 p-0 sm:items-center sm:p-4"
      role="presentation"
      onClick={(e) => {
        if (e.target === e.currentTarget && !busy) onClose();
      }}
    >
      <div
        className="flex w-full max-w-lg flex-col overflow-hidden rounded-t-2xl border border-[var(--mo-border)] bg-[var(--mo-surface-elevated)] shadow-2xl sm:rounded-2xl"
        role="dialog"
        aria-modal
        aria-label={title}
      >
        <header className="flex items-start justify-between gap-3 border-b border-[var(--mo-border)] px-4 py-3">
          <div>
            <h2 className="text-base font-semibold text-[var(--mo-text)] sm:text-lg">{title}</h2>
            <p className="mt-0.5 text-xs mo-muted">{hint}</p>
          </div>
          <button
            type="button"
            className="btn-secondary !min-h-10 !min-w-10 px-3 text-sm"
            disabled={busy}
            onClick={onClose}
            aria-label="Закрыть"
          >
            ✕
          </button>
        </header>

        <div className="space-y-2 px-4 py-3">
          <label className="block text-[11px] mo-muted">
            Причина
            <textarea
              ref={textareaRef}
              className="mo-input mt-1 min-h-[120px] w-full resize-y text-base leading-relaxed"
              value={reason}
              disabled={busy}
              onChange={(e) => {
                setReason(e.target.value);
                if (error) setError("");
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                  e.preventDefault();
                  submit();
                }
              }}
              placeholder="Например: дорого / выбрал другого / не актуально…"
              enterKeyHint="done"
            />
          </label>
          {error ? <p className="text-sm text-[var(--mo-danger)]">{error}</p> : null}
        </div>

        <footer className="flex gap-2 border-t border-[var(--mo-border)] px-4 py-3 pb-[max(0.75rem,env(safe-area-inset-bottom))]">
          <button type="button" className="btn-secondary min-h-12 flex-1 text-base" disabled={busy} onClick={onClose}>
            Отмена
          </button>
          <button
            type="button"
            className="min-h-12 flex-1 rounded-xl bg-[var(--mo-danger)] px-4 text-base font-semibold text-white disabled:opacity-50"
            disabled={busy}
            onClick={submit}
          >
            {busy ? "Сохранение…" : confirmLabel}
          </button>
        </footer>
      </div>
    </div>
  );
}
