"use client";
import { useEffect, useRef, useState } from "react";
import { Building2, Check, ChevronsUpDown, Search } from "lucide-react";
import type { CompanySummary } from "../lib/types";

export function CompanyPicker({ id, companies, selected, onSelect }: { id: string; companies: CompanySummary[] | null; selected: string; onSelect: (id: string) => void }) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const search = useRef<HTMLInputElement>(null);
  const activeOption = useRef<HTMLButtonElement>(null);
  const matches = companies?.filter(company => company.name.toLowerCase().includes(query.trim().toLowerCase())) ?? [];
  const name = companies?.find(company => company.id === selected)?.name;
  const placeholder = companies === null ? "Loading companies…" : companies.length ? "Choose a company" : "No companies available";

  useEffect(() => {
    if (!open) return;
    search.current?.focus();
    const dismiss = (event: PointerEvent) => { if (!root.current?.contains(event.target as Node)) setOpen(false); };
    document.addEventListener("pointerdown", dismiss);
    return () => document.removeEventListener("pointerdown", dismiss);
  }, [open]);
  useEffect(() => { if (open) activeOption.current?.scrollIntoView({ block: "nearest" }); }, [open, active, query]);

  function close() { setOpen(false); trigger.current?.focus(); }
  function choose(company: CompanySummary) { close(); onSelect(company.id); }
  function show() {
    setQuery(""); setActive(Math.max(0, companies?.findIndex(company => company.id === selected) ?? 0)); setOpen(true);
  }

  return <div className="sidebar-company-picker" ref={root}
    onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false); }}
    onKeyDown={event => { if (open && event.key === "Escape") { event.preventDefault(); event.stopPropagation(); close(); } }}>
    <span className="sr-only" id={`${id}-label`}>Company</span>
    <button type="button" className="company-picker-trigger" id={id} ref={trigger} disabled={!companies?.length}
      aria-labelledby={`${id}-label ${id}-value`} aria-haspopup="dialog" aria-expanded={open} aria-controls={open ? `${id}-panel` : undefined}
      onClick={() => open ? close() : show()} onKeyDown={event => { if (event.key === "ArrowDown" || event.key === "ArrowUp") { event.preventDefault(); show(); } }}>
      <Building2 aria-hidden/><span id={`${id}-value`}>{name ?? placeholder}</span><ChevronsUpDown aria-hidden/>
    </button>
    {open && <div className="company-picker-panel" id={`${id}-panel`} role="dialog" aria-label="Choose a company">
      <div className="company-picker-search"><Search aria-hidden/><input ref={search} role="combobox" aria-label="Search companies" placeholder="Search companies…"
        autoComplete="off" spellCheck={false} value={query} aria-expanded="true" aria-autocomplete="list" aria-controls={`${id}-options`}
        aria-activedescendant={matches[active] ? `${id}-option-${matches[active].id}` : undefined}
        onChange={event => { setQuery(event.target.value); setActive(0); }}
        onKeyDown={event => {
          if (event.nativeEvent.isComposing) return;
          if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault(); setActive(current => Math.max(0, Math.min(matches.length - 1, current + (event.key === "ArrowDown" ? 1 : -1))));
          } else if (event.key === "Enter") { event.preventDefault(); if (matches[active]) choose(matches[active]); }
        }}/></div>
      <div className="company-picker-options" id={`${id}-options`} role="listbox" aria-label="Companies">
        {matches.map((company, index) => <button type="button" role="option" id={`${id}-option-${company.id}`} key={company.id}
          className="company-picker-option" aria-selected={company.id === selected} data-active={index === active} tabIndex={-1}
          ref={index === active ? activeOption : undefined} onMouseDown={event => event.preventDefault()} onClick={() => choose(company)}>
          <Building2 aria-hidden/><span>{company.name}</span>{company.id === selected && <Check aria-hidden/>}
        </button>)}
      </div>
      {!matches.length && <p className="company-picker-empty" role="status">No companies found.</p>}
    </div>}
  </div>;
}
