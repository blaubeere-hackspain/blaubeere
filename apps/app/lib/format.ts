export function money(cents: number, currency = "EUR", compact = false, exact = false) {
  return new Intl.NumberFormat("en-IE", { style: "currency", currency, maximumFractionDigits: compact ? 1 : exact ? 2 : 0, ...(exact ? { minimumFractionDigits: 0 } : {}), notation: compact ? "compact" : "standard" }).format(cents / 100);
}
export function date(value: string, year = false) {
  return new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", ...(year ? { year: "numeric" } : {}), timeZone: "UTC" }).format(new Date(`${value}T00:00:00Z`));
}
export function addDays(value: string, days: number) {
  const day = new Date(`${value}T00:00:00Z`); day.setUTCDate(day.getUTCDate() + days); return day.toISOString().slice(0, 10);
}
export function cents(value: FormDataEntryValue | null, label: string): number {
  const text = String(value ?? "").trim();
  if (!/^\d+(\.\d{1,2})?$/.test(text)) throw new Error(`${label}: use a positive amount with at most two decimal places.`);
  const [whole, fraction = ""] = text.split(".");
  const result = Number(whole) * 100 + Number(fraction.padEnd(2, "0"));
  if (!Number.isSafeInteger(result) || result > 1_000_000_000_000) throw new Error(`${label} exceeds the supported amount.`);
  return result;
}
