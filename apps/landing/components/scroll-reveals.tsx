"use client";

import { useEffect } from "react";

export function observeScrollReveals() {
  const motion = window.matchMedia("(prefers-reduced-motion: reduce)");
  if (motion.matches || !("IntersectionObserver" in window)) return;

  const elements = document.querySelectorAll<HTMLElement>("[data-scroll-reveal]");
  const observer = new IntersectionObserver(entries => {
    for (const entry of entries) {
      const element = entry.target as HTMLElement;
      if (!entry.isIntersecting || element.dataset.scrollState !== "pending") continue;
      element.dataset.scrollState = "revealed";
      observer.unobserve(entry.target);
    }
  }, { rootMargin: "0px 0px -24px 0px", threshold: 0 });

  // Keep server-rendered and already-visible content in place, including deep links.
  for (const element of elements) {
    if (element.getBoundingClientRect().top < window.innerHeight || element.contains(document.activeElement)) continue;
    element.dataset.scrollState = "pending";
    observer.observe(element);
  }

  const revealFocused = (event: FocusEvent) => {
    const element = (event.target as Element).closest<HTMLElement>("[data-scroll-reveal]");
    if (!element) return;
    delete element.dataset.scrollState;
    observer.unobserve(element);
  };
  const cleanup = () => {
    observer.disconnect();
    for (const element of elements) delete element.dataset.scrollState;
    document.removeEventListener("focusin", revealFocused);
    motion.removeEventListener("change", cleanup);
  };
  document.addEventListener("focusin", revealFocused);
  motion.addEventListener("change", cleanup);
  return cleanup;
}

export function ScrollReveals() {
  useEffect(observeScrollReveals, []);
  return null;
}
