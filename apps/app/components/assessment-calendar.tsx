"use client";
import { useEffect, useRef, useState } from "react";
import { CalendarDays, ChevronLeft, ChevronRight, ChevronsUpDown } from "lucide-react";
const monthLabel = (value: string) => new Intl.DateTimeFormat("en-GB", { month: "long", year: "numeric", timeZone: "UTC" }).format(new Date(`${value}T00:00:00Z`));

const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function AssessmentCalendar({ dates, selected, onSelect }: { dates: string[]; selected: string; onSelect: (date: string) => void }) {
  const [open, setOpen] = useState(false);
  const [year, setYear] = useState((selected || dates.at(-1) || "").slice(0, 4));
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const years = [...new Set(dates.map(value => value.slice(0, 4)))].sort();
  const yearIndex = years.indexOf(year);

  useEffect(() => {
    if (!open) return;
    const selectedButton = panel.current?.querySelector<HTMLButtonElement>('button[aria-pressed="true"]') ?? panel.current?.querySelector<HTMLButtonElement>('.assessment-months button:not(:disabled)');
    selectedButton?.focus({ preventScroll: true });
    const dismiss = (event: PointerEvent) => { if (!root.current?.contains(event.target as Node)) setOpen(false); };
    document.addEventListener("pointerdown", dismiss);
    return () => document.removeEventListener("pointerdown", dismiss);
  }, [open, year]);
  function close() { setOpen(false); trigger.current?.focus(); }
  function changeYear(value: string) { panel.current?.focus({ preventScroll: true }); setYear(value); }

  return <div className="assessment-calendar" ref={root}
    onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false); }}
    onKeyDown={event => { if (event.key === "Escape" && open) { event.preventDefault(); event.stopPropagation(); close(); } }}>
    <button type="button" id="model-date" ref={trigger} className="assessment-calendar-trigger" disabled={!dates.length}
      aria-label={`Selected month: ${selected ? monthLabel(selected) : "Choose a month"}`} aria-haspopup="dialog" aria-expanded={open} aria-controls={open ? "assessment-calendar-panel" : undefined}
      onClick={() => { if (open) close(); else { setYear((selected || dates.at(-1) || "").slice(0, 4)); setOpen(true); } }}>
      <CalendarDays aria-hidden/><span>{selected ? monthLabel(selected) : "Choose a month"}</span><ChevronsUpDown aria-hidden/>
    </button>
    {open && <div className="assessment-calendar-panel" id="assessment-calendar-panel" ref={panel} role="dialog" tabIndex={-1} aria-label="Choose a month" aria-describedby="assessment-calendar-note">
      <div className="assessment-calendar-year"><button type="button" className="icon-button" aria-label="Previous year" disabled={yearIndex <= 0} onClick={() => changeYear(years[yearIndex - 1])}><ChevronLeft aria-hidden/></button><strong aria-live="polite">{year}</strong><button type="button" className="icon-button" aria-label="Next year" disabled={yearIndex >= years.length - 1} onClick={() => changeYear(years[yearIndex + 1])}><ChevronRight aria-hidden/></button></div>
      <div className="assessment-months" role="group" aria-label={`Assessments in ${year}`}>{months.map((month, index) => {
        const cutoff = dates.find(value => value.startsWith(`${year}-${String(index + 1).padStart(2, "0")}-`));
        return <button type="button" key={month} disabled={!cutoff} aria-label={`${month} ${year}${cutoff ? "" : ": no company data"}`} aria-pressed={Boolean(cutoff && cutoff === selected)} onClick={() => { if (cutoff) { close(); onSelect(cutoff); } }}>{month}{!cutoff && <small>No data</small>}</button>;
      })}</div>
      <p className="assessment-calendar-note" id="assessment-calendar-note">Months without company data can’t be selected.</p>
    </div>}
  </div>;
}
