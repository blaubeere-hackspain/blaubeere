import assert from "node:assert/strict";
import { observeScrollReveals } from "../apps/landing/components/scroll-reveals";

// Exercise the observer lifecycle without adding a DOM or animation dependency.
const card = (top: number) => ({
  dataset: {} as Record<string, string>,
  getBoundingClientRect: () => ({ top }),
  contains: (_element: unknown) => false,
});
const focused = {};
const above = card(-100), visible = card(300), first = card(1000), second = card(1400);
const active = card(1800);
active.contains = element => element === focused;
const elements = [above, visible, first, second, active];
const page = Object.assign(new EventTarget(), { querySelectorAll: () => elements, activeElement: focused });
const motion = Object.assign(new EventTarget(), { matches: false });
let observer: Observer;
class Observer {
  watched = new Set<unknown>();
  constructor(public callback: (entries: unknown[]) => void) { observer = this; }
  observe(element: unknown) { this.watched.add(element); }
  unobserve(element: unknown) { this.watched.delete(element); }
  disconnect() { this.watched.clear(); }
}
const replacements = {
  window: { innerHeight: 800, matchMedia: () => motion, IntersectionObserver: Observer },
  document: page,
  IntersectionObserver: Observer,
};
const original = Object.fromEntries(Object.keys(replacements).map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
try {
  for (const [key, value] of Object.entries(replacements)) Object.defineProperty(globalThis, key, { value, configurable: true });
  assert.ok(elements.every(element => !element.dataset.scrollState), "Content starts visible without JavaScript");
  const cleanup = observeScrollReveals();
  assert.deepEqual([above, visible, active].map(element => element.dataset.scrollState), [undefined, undefined, undefined], "Never hide visible, restored or already-focused content");
  assert.equal(first.dataset.scrollState, "pending");
  observer!.callback([{ target: first, isIntersecting: false }]);
  assert.equal(first.dataset.scrollState, "pending");
  observer!.callback([{ target: first, isIntersecting: true }]);
  assert.equal(first.dataset.scrollState, "revealed");
  assert.equal(observer!.watched.has(first), false, "Reveal once, then stop observing");
  const focus = new Event("focusin");
  Object.defineProperty(focus, "target", { value: { closest: () => second } });
  page.dispatchEvent(focus);
  assert.equal(second.dataset.scrollState, undefined, "Keyboard focus reveals immediately without animation");
  observer!.callback([{ target: second, isIntersecting: true }]);
  assert.equal(second.dataset.scrollState, undefined, "A queued intersection must not restart animation after focus");
  cleanup!();
  assert.equal(observer!.watched.size, 0);
  assert.ok(elements.every(element => !element.dataset.scrollState), "Unmount restores all content");
  observeScrollReveals();
  motion.matches = true;
  motion.dispatchEvent(new Event("change"));
  assert.ok(elements.every(element => !element.dataset.scrollState), "Changing motion preference immediately reveals pending content");
  assert.equal(observer!.watched.size, 0);
  assert.equal(observeScrollReveals(), undefined, "Reduced motion does not set up animations");
  motion.matches = false;
  delete (replacements.window as Partial<typeof replacements.window>).IntersectionObserver;
  assert.equal(observeScrollReveals(), undefined, "Unsupported browsers keep static content");
} finally {
  for (const [key, descriptor] of Object.entries(original)) {
    if (descriptor) Object.defineProperty(globalThis, key, descriptor);
    else Reflect.deleteProperty(globalThis, key);
  }
}
console.log("Landing scroll checks passed: visibility, one-time entry, focus, cleanup and reduced motion.");
