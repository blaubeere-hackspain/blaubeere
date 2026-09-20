import type { Company, HealthAssessment } from "./types";

export type HealthPoint = { date: string; score: number; assessment?: HealthAssessment };

export function healthTimeline(company: Company): HealthPoint[] {
  // Older score-only records stay inspectable without borrowing today's explanation.
  const points = new Map<string, HealthPoint>(company.history.map(point => [point.date, { date: point.date, score: point.score }]));
  points.set(company.assessment_date, { date: company.assessment_date, score: company.health.score, assessment: {
    date: company.assessment_date, health: company.health, model_version: company.model_version,
    history_mode: company.history_mode, drivers: company.drivers, coverage: company.coverage, flows: company.flows,
  } });
  for (const assessment of company.health_assessments ?? []) points.set(assessment.date, { date: assessment.date, score: assessment.health.score, assessment });
  return [...points.values()].filter(point => point.date <= company.assessment_date).sort((a, b) => a.date.localeCompare(b.date));
}

export function healthHref(companyId: string, date: string, demo = false) {
  return `${demo ? "/demo" : "/dashboard"}/health/${encodeURIComponent(date)}?company=${encodeURIComponent(companyId)}`;
}

export function validHealthDate(value: string) {
  return /^\d{4}-\d{2}-\d{2}$/.test(value) && Number.isFinite(Date.parse(value)) && new Date(value).toISOString().slice(0, 10) === value;
}
