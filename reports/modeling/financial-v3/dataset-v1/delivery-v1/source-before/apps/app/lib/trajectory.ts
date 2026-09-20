import type { TrajectoryPoint } from "./trajectory-types";
export type Sample = { date: string; value: number | null };
export function segments(samples: Sample[]): { index: number; date: string; value: number }[][] {
  const result: { index: number; date: string; value: number }[][] = [];
  let current: { index: number; date: string; value: number }[] = [];
  for (const [index, sample] of samples.entries()) {
    const previous = samples[index - 1];
    const month = (date: string) => Number(date.slice(0, 4)) * 12 + Number(date.slice(5, 7));
    if (sample.value === null || !Number.isFinite(sample.value) || (previous && month(sample.date) - month(previous.date) !== 1)) {
      if (current.length) result.push(current);
      current = [];
    }
    if (sample.value !== null && Number.isFinite(sample.value)) current.push({ index, date: sample.date, value: sample.value });
  }
  if (current.length) result.push(current);
  return result;
}
export function observedRatio(point: TrajectoryPoint): number | null {
  const m = point.evidence.monthly;
  return m.input_eligible && m.gross_eur !== null && m.gross_eur > 0 && m.receipts_eur !== null && m.payments_eur !== null
    ? (m.receipts_eur - m.payments_eur) / m.gross_eur : null;
}
export function precision(value: number | null): string {
  return value === null || !Number.isFinite(value) ? "Unavailable" : String(value);
}
export function closedMonth(value: string, available: string[]): boolean {
  return /^\d{4}-\d{2}-\d{2}$/.test(value) && available.includes(value);
}
