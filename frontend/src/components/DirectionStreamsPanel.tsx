import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import toast from "react-hot-toast";

import { apiFetch } from "@/lib/api";
import type { BookingDirection } from "@/lib/types";

/** CRM-DN услуги записи (вместо клинических «потоков курсов»). */
export const CRM_DN_DEFAULT_PRODUCTS = [
  { name: "Интенсив", duration_min: 60 },
  { name: "Курс «Мисли Дилноза»", duration_min: 60 },
  { name: "Консультация", duration_min: 60 },
] as const;

export function DirectionStreamsPanel() {
  const qc = useQueryClient();
  const [pipelineId, setPipelineId] = useState<number | "">("");
  const [newName, setNewName] = useState("");
  const [newDuration, setNewDuration] = useState(60);

  const pipelinesQ = useQuery({
    queryKey: ["pipelines"],
    queryFn: () => apiFetch<{ id: number; name: string }[]>("/api/pipelines"),
  });

  const directionsQ = useQuery({
    queryKey: ["booking-directions", pipelineId],
    enabled: pipelineId !== "",
    queryFn: () => apiFetch<BookingDirection[]>(`/api/booking/directions?pipeline_id=${pipelineId}`),
  });

  useEffect(() => {
    if (pipelineId === "" && (pipelinesQ.data?.length ?? 0) > 0) {
      setPipelineId(pipelinesQ.data![0].id);
    }
  }, [pipelineId, pipelinesQ.data]);

  function invalidate() {
    void qc.invalidateQueries({ queryKey: ["booking-directions"] });
    void qc.invalidateQueries({ queryKey: ["booking-directions-all"] });
  }

  const createMut = useMutation({
    mutationFn: (body: { name: string; duration_min: number }) => {
      if (pipelineId === "") throw new Error("Выберите воронку");
      const name = body.name.trim();
      if (!name) throw new Error("Укажите название услуги");
      const duration = Math.min(480, Math.max(10, Number(body.duration_min) || 60));
      return apiFetch<BookingDirection>("/api/booking/directions", {
        method: "POST",
        body: JSON.stringify({
          name,
          duration_min: duration,
          pipeline_id: pipelineId,
        }),
      });
    },
    onSuccess: (row) => {
      toast.success(`Услуга «${row.name}» добавлена`);
      setNewName("");
      setNewDuration(60);
      invalidate();
    },
    onError: (e: Error) => toast.error(e.message || "Не удалось добавить услугу"),
  });

  const seedMut = useMutation({
    mutationFn: async () => {
      if (pipelineId === "") throw new Error("Выберите воронку");
      const existing = new Set(
        (directionsQ.data ?? []).filter((d) => d.is_active).map((d) => d.name.trim().toLowerCase()),
      );
      let created = 0;
      for (const p of CRM_DN_DEFAULT_PRODUCTS) {
        if (existing.has(p.name.toLowerCase())) continue;
        await apiFetch<BookingDirection>("/api/booking/directions", {
          method: "POST",
          body: JSON.stringify({
            name: p.name,
            duration_min: p.duration_min,
            pipeline_id: pipelineId,
          }),
        });
        created += 1;
      }
      return created;
    },
    onSuccess: (n) => {
      if (n === 0) toast.success("Базовые услуги уже есть");
      else toast.success(`Добавлено услуг: ${n}`);
      invalidate();
    },
    onError: (e: Error) => toast.error(e.message || "Не удалось добавить услуги"),
  });

  const archiveMut = useMutation({
    mutationFn: (id: number) =>
      apiFetch<void>(`/api/booking/directions/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      toast.success("Услуга скрыта");
      invalidate();
    },
    onError: (e: Error) => toast.error(e.message || "Не удалось скрыть"),
  });

  function submitManual() {
    createMut.mutate({ name: newName, duration_min: newDuration });
  }

  const products = (directionsQ.data ?? []).filter((d) => d.is_active);
  const canSubmit = pipelineId !== "" && Boolean(newName.trim()) && !createMut.isPending;

  return (
    <section className="mo-section space-y-3 p-3 sm:p-4">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div className="min-w-0 max-w-2xl">
          <h2 className="text-sm font-semibold text-[var(--mo-text)] sm:text-base">Услуги</h2>
          <p className="mt-0.5 text-[11px] leading-snug mo-muted">
            Список для онлайн-записи. Можно добавить своё название вручную или взять базовые:{" "}
            <b className="text-[var(--mo-text)]">Интенсив</b>,{" "}
            <b className="text-[var(--mo-text)]">Курс «Мисли Дилноза»</b>, консультация.
          </p>
        </div>
        <label className="flex items-center gap-2 text-[11px] mo-muted">
          Воронка
          <select
            className="mo-input py-1.5 text-sm"
            value={pipelineId}
            onChange={(e) => setPipelineId(e.target.value ? Number(e.target.value) : "")}
          >
            <option value="">—</option>
            {(pipelinesQ.data ?? []).map((p) => (
              <option key={p.id} value={p.id}>
                {p.name === "Sales funnel" ? "Маркетинг" : p.name}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="rounded-xl border border-[var(--mo-accent)]/25 bg-[var(--mo-accent-soft)]/40 p-3">
        <p className="text-xs font-semibold text-[var(--mo-text)]">Добавить услугу вручную</p>
        <p className="mt-0.5 text-[11px] mo-muted">
          Введите любое название — оно сразу появится в выборе услуги при записи.
        </p>
        <div className="mt-2 flex flex-wrap items-end gap-2">
          <label className="min-w-[14rem] flex-1 text-[11px] mo-muted">
            Название
            <input
              className="mo-input mt-1 w-full text-sm"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  if (canSubmit) submitManual();
                }
              }}
              placeholder="Например: Интенсив или своя услуга"
              autoComplete="off"
            />
          </label>
          <label className="w-24 text-[11px] mo-muted">
            Мин
            <input
              type="number"
              min={10}
              max={480}
              step={5}
              className="mo-input mt-1 w-full text-sm tabular-nums"
              value={newDuration}
              onChange={(e) => setNewDuration(Number(e.target.value) || 60)}
            />
          </label>
          <button
            type="button"
            className="btn-primary px-3 py-2 text-sm"
            disabled={!canSubmit}
            onClick={submitManual}
          >
            {createMut.isPending ? "…" : "Добавить"}
          </button>
          <button
            type="button"
            className="btn-secondary px-3 py-2 text-sm"
            disabled={pipelineId === "" || seedMut.isPending}
            onClick={() => seedMut.mutate()}
          >
            {seedMut.isPending ? "…" : "Базовые услуги"}
          </button>
        </div>
      </div>

      {directionsQ.isLoading && <p className="text-sm lux-caption">Загрузка…</p>}

      {!directionsQ.isLoading && products.length === 0 && pipelineId !== "" && (
        <p className="text-sm mo-muted">
          Пока пусто. Введите название выше и нажмите «Добавить», либо «Базовые услуги».
        </p>
      )}

      {pipelineId === "" && (
        <p className="text-sm mo-muted">Сначала выберите воронку — затем можно добавить услугу.</p>
      )}

      {products.length > 0 ? (
        <ul className="divide-y divide-[var(--mo-border)] overflow-hidden rounded-xl border border-[var(--mo-border)] bg-[var(--mo-surface-elevated)]">
          {products.map((d) => (
            <li
              key={d.id}
              className="flex flex-wrap items-center justify-between gap-2 px-3 py-2.5 text-sm text-[var(--mo-text)]"
            >
              <div className="min-w-0">
                <p className="truncate font-medium">{d.name}</p>
                <p className="text-[11px] mo-muted">{d.duration_min ?? "—"} мин</p>
              </div>
              <button
                type="button"
                className="text-xs font-medium text-rose-600 hover:underline disabled:opacity-50"
                disabled={archiveMut.isPending}
                onClick={() => {
                  if (!window.confirm(`Скрыть услугу «${d.name}»?`)) return;
                  archiveMut.mutate(d.id);
                }}
              >
                Скрыть
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
