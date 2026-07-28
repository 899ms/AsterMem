/**
 * Background: The timeline tab displays the /api/timeline/events event stream, grouped by date,
 * supporting check-to-complete and delete.
 * Design intent: Default fetch window is past 30 days to future 60 days, adjustable via date inputs;
 * complete/delete use optimistic updates with rollback on failure, reducing full-list refreshes.
 * Key constraint: Complete endpoint follows POST /api/timeline/events/{id}/complete convention;
 * if backend 404s, the unified error toast handles it (field and endpoint tolerance is a design premise for this page).
 */
import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { IconTrash } from "@tabler/icons-react";
import { EmptyState, LoadingLine } from "../../components/EmptyState";
import { api, reportError } from "../../api";
import { useI18n } from "../../i18n";
import { useAuthSnapshot } from "../../authState";
import type { TimelineEvent } from "../../types";

function isoDate(offsetDays: number): string {
  const d = new Date();
  d.setDate(d.getDate() + offsetDays);
  return d.toISOString().slice(0, 10);
}

export function TimelineTab() {
  const { t } = useI18n();
  const { demoMode } = useAuthSnapshot();
  const navigate = useNavigate();
  const [start, setStart] = useState(isoDate(-30));
  const [end, setEnd] = useState(isoDate(60));
  const [events, setEvents] = useState<TimelineEvent[]>([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api<unknown>("GET", `/api/timeline/events?start_date=${start}&end_date=${end}`);
      const list = Array.isArray(res) ? res : (res as { events?: unknown })?.events;
      setEvents(Array.isArray(list) ? (list as TimelineEvent[]) : []);
    } catch (err) {
      reportError(err, t("Unable to load timeline events"));
      setEvents([]);
    } finally {
      setLoading(false);
    }
  }, [start, end, t]);

  useEffect(() => {
    void load();
  }, [load]);

  const eventDate = (e: TimelineEvent) => (e.absolute_time || "").slice(0, 10) || t("Undated");
  const isDone = (e: TimelineEvent) => e.status === "completed";
  // event_summary is the raw text snippet around the time expression, may contain markdown symbols and newlines
  const eventTitle = (e: TimelineEvent) => {
    const cleaned = String(e.event_summary || "").replace(/[#*`>]/g, "").replace(/\s+/g, " ").trim();
    return cleaned || e.original_text || t("Untitled");
  };

  const toggleComplete = async (event: TimelineEvent) => {
    if (event.id === undefined) return;
    const prev = events;
    const done = isDone(event);
    setEvents((list) => list.map((e) => (e.id === event.id ? { ...e, status: done ? "pending" : "completed" } : e)));
    try {
      await api("POST", `/api/timeline/events/${event.id}/${done ? "uncomplete" : "complete"}`, {});
    } catch (err) {
      setEvents(prev);
      reportError(err, t("Unable to update the event"));
    }
  };

  const remove = async (event: TimelineEvent) => {
    if (event.id === undefined) return;
    const prev = events;
    setEvents((list) => list.filter((e) => e.id !== event.id));
    try {
      await api("DELETE", `/api/timeline/events/${event.id}`);
    } catch (err) {
      setEvents(prev);
      reportError(err, t("Unable to delete the event"));
    }
  };

  const grouped = new Map<string, TimelineEvent[]>();
  for (const e of events) {
    const key = eventDate(e);
    grouped.set(key, [...(grouped.get(key) ?? []), e]);
  }
  const days = [...grouped.keys()].sort();

  return (
    <div>
      <div style={{ display: "flex", gap: 12, marginBottom: 20, alignItems: "end", flexWrap: "wrap" }}>
        <label className="field">
          <span>{t("From")}</span>
          <input className="input mono" type="date" value={start} onChange={(e) => setStart(e.target.value)} />
        </label>
        <label className="field">
          <span>{t("To")}</span>
          <input className="input mono" type="date" value={end} onChange={(e) => setEnd(e.target.value)} />
        </label>
      </div>
      {loading ? (
        <LoadingLine label={t("Loading")} />
      ) : days.length === 0 ? (
        <EmptyState message={t("No events in this range")} />
      ) : (
        days.map((day) => (
          <div key={day} className="timeline-day">
            <span className="kicker" style={{ display: "block", marginBottom: 8 }}>{day}</span>
            {(grouped.get(day) ?? []).map((event, i) => (
              <div key={String(event.id ?? i)} className={`timeline-event ${isDone(event) ? "done" : ""}`}
                style={{ cursor: event.document_id ? "pointer" : undefined }}
                onClick={(e) => {
                  if (!event.document_id) return;
                  if ((e.target as HTMLElement).closest("input, button")) return;
                  navigate(`/view/${event.document_id}`);
                }}>
                {!demoMode && (
                  <input type="checkbox" checked={isDone(event)} onChange={() => toggleComplete(event)}
                    style={{ marginTop: 4, accentColor: "var(--ink)" }} aria-label={t("Mark complete")} />
                )}
                <div style={{ flex: 1 }}>
                  <strong style={{ fontSize: 14 }}>{eventTitle(event)}</strong>
                  {event.document_title && (
                    <p className="muted" style={{ fontSize: 12.5 }}>
                      {t("From memory")}: {event.document_title}
                      {event.original_text ? ` · ${event.original_text}` : ""}
                    </p>
                  )}
                </div>
                {event.is_expired && !isDone(event) && (
                  <span className="mono-sm text-danger">{t("Expired")}</span>
                )}
                {!demoMode && (
                  <button type="button" onClick={() => remove(event)} aria-label={t("Delete")} style={{ opacity: 0.5 }}>
                    <IconTrash size={15} stroke={1.5} />
                  </button>
                )}
              </div>
            ))}
          </div>
        ))
      )}
    </div>
  );
}
