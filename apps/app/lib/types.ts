export type Point = { date: string; cash_cents: number };
export type Forecast = {
  points: Point[]; checkpoints: (Point & { day: number })[]; minimum: Point;
  first_shortfall: Point | null; funding_needed_cents: number; closing_cash_cents: number;
  buffer_cents: number; horizon_days: number;
};
export type CompanySummary = { id: string; name: string; group: string; currency: string; data_mode: string };
export type Flow = { id: string; label: string; amount_cents: number; settled_cents: number; date: string; known_on: string; kind: string; timing: string; source: string };
export type HealthAssessment = {
  date: string; model_version: string; history_mode: string;
  health: { score: number; previous_score: number | null; period: string; label: string; note: string };
  drivers: { label: string; detail: string; points: number | null; source_ids: string[] }[];
  coverage: { label: string; status: string; as_of: string | null; detail: string }[];
  flows: Flow[];
};
export type Company = CompanySummary & {
  assessment_date: string; model_version: string; history_mode: string;
  opening_cash_cents: number; buffer_cents: number;
  health: { score: number; previous_score: number; period: string; label: string; note: string };
  history: (Point & { score: number })[];
  drivers: { label: string; detail: string; points: number; source_ids: string[] }[];
  coverage: { label: string; status: string; as_of: string | null; detail: string }[];
  flows: Flow[];
  health_assessments?: HealthAssessment[];
};
export type Assessment = { company: Company; forecast: Forecast };
export type Identity = { email: string; company_ids: string[]; mcp_resource: string };
export type Goal = {
  metric: string; target_cents: number; deadline: string; cash_floor_cents: number;
  max_collection_days: number; max_spend_reduction_pct: number; max_funding_cents: number; max_growth_pct: number;
  business: { monthly_revenue_cents: number; gross_margin_pct: number; collection_days: number } | null;
};
export type Plan = {
  id: string; title: string; changes: { collection_days: number; spend_reduction_pct: number; funding_cents: number; growth_pct: number };
  value_cents: number; target_met: boolean; cash_floor_met: boolean; qualifies: boolean;
  remaining_target_cents: number; remaining_cash_cents: number; cash_impact_cents: number; forecast: Forecast; trade_off: string;
};
export type Comparison = {
  goal: Goal; baseline: Forecast; baseline_value_cents: number; plans: Plan[];
  any_qualifies: boolean; explanation: string; assumptions: string[];
};
