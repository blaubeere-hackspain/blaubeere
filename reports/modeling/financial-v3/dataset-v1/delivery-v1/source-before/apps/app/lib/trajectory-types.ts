import type { CompanySummary } from "./types";
export type RatioBounds = { lower_ratio: number | null; upper_ratio: number | null };
export type Bounds = RatioBounds & { lower_eur: number | null; upper_eur: number | null };
export type WindowEvidence = { months: string[]; base: RatioBounds; trim: RatioBounds; gross_eur: number | null; receipts_ratio: number | null; payments_ratio: number | null; unknown_in_ratio: number | null; unknown_out_ratio: number | null; explanation_reasons: string[] };
export type TrajectoryPoint = {
  month: string; as_of: string; operating_state: "deficit" | "non_deficit" | "insufficient_evidence";
  state_reasons: string[]; direction: "improving" | "stable" | "deteriorating" | "insufficient_evidence";
  direction_reasons: string[]; monthly_input_eligible: boolean; direction_input_eligible: boolean;
  evidence: {
    monthly: { input_eligible: boolean; reasons: string[]; gross_eur: number | null; cash_activity: boolean | null; n_sin_eur: number | null; base: Bounds; trim: Bounds; receipts_eur: number | null; payments_eur: number | null; unknown_in_eur: number | null; unknown_out_eur: number | null; explanation_reasons: string[] };
    prior: WindowEvidence; recent: WindowEvidence;
    delta_intervals: { base: { lower: number | null; upper: number | null }; trim: { lower: number | null; upper: number | null } };
    components: { receipts_delta_ratio: number | null; payments_delta_ratio: number | null; identified_net_delta_ratio: number | null; receipt_contribution_ratio: number | null; payment_contribution_ratio: number | null; gross_delta_eur: number | null };
    explanation_reasons: string[]; source_refs: [string, string][];
  };
  coverage: { valid_months_prior: number; valid_months_recent: number; valid_months_total: number; failure_reasons: { month: string; reasons: string[] }[] };
  signal: { status: "none" | "insufficient_evidence" | "watch" | "confirmed"; sign: "improvement" | "deterioration" | null; first_signal_at: string | null; confirmed_alert_at: string | null; episode_id: string | null };
  probability: { p_proxy: number | null; eligible: boolean | null; horizon_start: string; horizon_end: string; unavailable_reasons: string[]; coverage: Record<string, number | string[]>; missing_feature_reasons: Record<string, string> };
  review_action: { code: string; reason: string; evidence_ref: "evidence"; executable: false };
};
export type TrajectoryCompany = CompanySummary & {
  kind: "operating_trajectory"; assessment_date: string; model_version: string; history_mode: "reconstructed";
  opening_cash_cents: null; buffer_cents: null; health: null; predictive: null; history: []; flows: []; drivers: []; coverage: [];
  trajectory: {
    schema_version: 2; points: TrajectoryPoint[]; protocol_ref: string; model_ref: string; review_actions_ref: string; cash_planning: "unavailable";
    source_provenance: { ref: string; company_id: string; group_assignment: "train" | "validation" | "final_test"; event_evaluation_included: boolean };
    backtest_summary: { ref: string; status: "anticipation_not_validated"; usage: "descriptive_review_only"; automatic_predictive_alerts_enabled: false };
    development_demo_case: { kind: string; company_id: string; onset_month: string; confirmed_at: string; case_visibility_from: string; pattern_months: string[]; proof: Record<string, unknown>; sealed_case: Record<string, unknown>; artifact_ref: string } | null;
  };
};
export type Metric = { value: number | null; reason: string | null };
export type Stratum = { sign: string; method: string; events: number; alerts: number; matches: number; misses: number; false_alarms: number; censored_unknown: number; recall: Metric; false_alert_share: Metric; lead_histogram: { "1": number; "2": number }; lead_median: Metric };
export type Interval = { ci95: [number, number] | null; reason: string | null; events: number; event_groups: number; denominator_groups: number; finite_replicates: number; undefined_replicates: number; conditioning: string };
export type BacktestSummary = {
  status: string; usage: string; automatic_predictive_alerts_enabled: false;
  phases: Record<string, { strata: Stratum[]; coverage: Record<string, unknown>; period: Record<string, unknown>; bootstrap: { strata: { sign: string; method: string; recall: Interval; false_alert_share: Interval }[] } }>;
};
export type TrajectoryAssessmentData = {
  company: TrajectoryCompany; forecast: null; cash_planning_available: false; cash_planning_reason: string;
  selected_as_of: string; available_as_of: string[]; current_point: TrajectoryPoint;
  trajectory_metadata: { report_timing: string; manifest_sha256: string; companies_sha256: string; protocol: Record<string, unknown>; model: Record<string, unknown>; source: Record<string, unknown>; review_actions: Record<string, unknown>; backtest_summary: BacktestSummary | null };
};
