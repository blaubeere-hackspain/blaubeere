"use client";
import { useEffect, useRef, type ReactNode } from "react";

export function outsideDialog(bounds: Pick<DOMRect, "left" | "right" | "top" | "bottom">, x: number, y: number) {
  return x < bounds.left || x > bounds.right || y < bounds.top || y > bounds.bottom;
}

export function Dialog({ open, onClose, titleId, className, children }: { open: boolean; onClose: () => void; titleId: string; className: string; children: ReactNode }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const backdropStart = useRef(false);
  useEffect(() => {
    if (open && dialog.current && !dialog.current.open) {
      dialog.current.dataset.instant = String(document.activeElement?.matches(":focus-visible") ?? false);
      dialog.current.showModal();
    }
    else if (!open) dialog.current?.close();
  }, [open]);
  return <dialog ref={dialog} className={className} aria-labelledby={titleId} onClose={onClose}
    onKeyDown={event => { if (event.key === "Escape" || event.key === "Enter" || event.key === " ") event.currentTarget.dataset.instant = "true"; }}
    onPointerDownCapture={event => { event.currentTarget.dataset.instant = "false"; }}
    onPointerDown={event => { backdropStart.current = event.target === event.currentTarget && outsideDialog(event.currentTarget.getBoundingClientRect(), event.clientX, event.clientY); }}
    onPointerCancel={() => { backdropStart.current = false; }}
    onClick={event => {
      if (backdropStart.current && event.target === event.currentTarget && outsideDialog(event.currentTarget.getBoundingClientRect(), event.clientX, event.clientY)) onClose();
      backdropStart.current = false;
    }}>{children}</dialog>;
}
