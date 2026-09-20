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
export type CashCompany = CompanySummary & {
  kind: "cash"; predictive: null;
  assessment_date: string; model_version: string; history_mode: string;
  opening_cash_cents: number; buffer_cents: number;
  health: { score: number; previous_score: number; period: string; label: string; note: string };
  history: (Point & { score: number })[];
  drivers: { label: string; detail: string; points: number; source_ids: string[] }[];
  coverage: { label: string; status: string; as_of: string | null; detail: string }[];
  flows: Flow[];
  health_assessments?: HealthAssessment[];
};
export type Predictive = {
  target: "target_deficit_3m"; definition: string; as_of: string;
  horizon_months: 3; horizon_start: string; horizon_end: string;
  probability_estimate: number | null; eligible: boolean; accepted: boolean;
  unavailable_reasons: string[];
  coverage: { eligibility_reasons: string[]; valid_months_3m: number; valid_months_6m: number; n_sin_eur_current: number };
  observed_features: Record<string, number | null>; missing_reason: Record<string, string>;
  feature_descriptions: Record<string, string>;
  model_version: string; calibrated: false; scope: string; currency_basis: string;
  validation: {
    labeled_rows: number; prospective_rows: number; groups: number;
    average_precision: number; prevalence: number; brier: number; baseline_brier: number;
    relative_brier_skill: number; relative_brier_skill_ci95: [number, number];
    interval_method: string; interval_sampling_unit: string; limits: string[];
  };
  provenance: { latest_predictions_sha256: string; model_manifest_sha256: string; final_report_sha256: string };
};
export type ProxyCompany = CompanySummary & {
  kind: "proxy_only"; assessment_date: string; model_version: string;
  history_mode: "reconstructed"; data_mode: "synthetic";
  opening_cash_cents: null; buffer_cents: null; health: null;
  history: []; drivers: []; coverage: []; flows: []; predictive: Predictive;
};
// Legacy health views consume cash-backed companies, not proxy assessments.
export type Company = CashCompany;
export type Assessment =
  | { company: CashCompany; forecast: Forecast; cash_planning_available: true; cash_planning_reason: null }
  | { company: ProxyCompany; forecast: null; cash_planning_available: false; cash_planning_reason: string };
export function hasProxyEstimate(p: Predictive): p is Predictive & { probability_estimate: number } {
  return p.accepted && p.eligible && typeof p.probability_estimate === "number"
    && Number.isFinite(p.probability_estimate) && p.probability_estimate >= 0 && p.probability_estimate <= 1;
}
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

export type DailyCash = {
  date: string; income: number | null; expense: number | null; balance: number | null; unknown_movements: number;
};
export type CashMonth = {
  currency: string; anchor_date: string; income: number | null; expense: number | null;
  closing_balance: number | null; days: DailyCash[];
};
export type ModelRecord = {
  daily_cash?: CashMonth[] | null;
  version: string; company_id: string; group_id: string; month: string; as_of: string;
  health_score: number | null; excluida: boolean; confidence: string; n_meses_ventana: number; n_meses_con_actividad: number;
  c6: number | null; p6: number | null; d6: number | null; t6_efectivo: number | null;
  deficit_servicio_6: number | null; obligacion_vencida_m: number | null;
  r_hist: number | null; colchon_v4: number | null; colchon_aplicable: number | null;
  mora_indice: number | null; multiplicador_deuda: number | null; h_antes_de_ajustes: number | null;
  penalizacion_mora_puntos: number | null; penalizacion_multiplicador_puntos: number | null;
  volumen_ambiguo_eur: number | null; volumen_ambiguo_pct: number | null;
  k: number; alpha: number; beta: number; reasons: string[];
  cash: {
    saldo_reversa_eur: number | null; flujo_neto: number | null; volumen_conocido: number | null; meses_de_cobertura_reversa: number | null; saldo_ancla_eur: number | null; confidence: string;
    flujo_operating_in: number | null; flujo_operating_out: number | null;
    flujo_financing_in: number | null; flujo_financing_out: number | null;
    flujo_investment_in: number | null; flujo_investment_out: number | null;
    flujo_transfer: number | null; flujo_non_economic: number | null; flujo_unknown: number | null;
    flags: string[];
  } | null;
  payment: {
    pago_exposicion_eur: number | null; pago_vencido_eur: number | null; mora_pago_robusta: number | null;
    cobro_exposicion_eur: number | null; cobro_vencido_eur: number | null; mora_cobro_robusta: number | null;
    pago_vencido_1_30_eur: number | null; pago_vencido_31_60_eur: number | null; pago_vencido_61_90_eur: number | null;
    pago_vencido_91_180_eur: number | null; pago_vencido_180_mas_eur: number | null; pago_n_facturas: number;
    cobro_vencido_1_30_eur: number | null; cobro_vencido_31_60_eur: number | null; cobro_vencido_61_90_eur: number | null;
    cobro_vencido_91_180_eur: number | null; cobro_vencido_180_mas_eur: number | null; cobro_n_facturas: number;
    mora_indice: number | null; confidence: string; confidence_pago: string; confidence_cobro: string;
    pago_n_huecos_eur: number; cobro_n_huecos_eur: number; n_vencimiento_desconocido: number;
  } | null;
  debt: {
    servicio_esperado_eur: number | null; servicio_observado_eur: number | null; deficit_servicio_eur: number | null;
    obligacion_vencida_eur: number | null; multiplicador_deuda: number | null; confidence: string;
  } | null;
};
export type ModelAssessment = {
  kind: "model"; company: CompanySummary; records: ModelRecord[];
  provenance: { batch_id: string; source_revision: string; imported_at: string;
    files: { table: string; path: string; sha256: string; rows: number }[];
    model_summary: { advertencia: string; limitaciones: string[] };
  };
};
