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

export type DailyCash = {
  date: string; income: number | null; expense: number | null; balance: number | null; unknown_movements: number;
};
export type CashMonth = {
  currency: string; anchor_date: string; income: number | null; expense: number | null;
  closing_balance: number | null; days: DailyCash[];
};
export type CashProjection = {
  version: string; as_of: string; currency: "EUR";
  horizons: {
    h: 30 | 60 | 90; date: string; saldo_corte_eur: number | null;
    entrada_esperada_eur: number | null; salida_esperada_eur: number | null;
    flujo_neto_esperado_eur: number | null; saldo_proyectado_eur: number | null;
  }[];
};
export type ModelRecord = {
  health_status?: {
    state: "attention_needed" | "insufficient_evidence" | "no_flags";
    score: number | null; confidence: string; low_score_threshold: number; policy_note: string;
    issues: { code: string; severity: string; title: string; detail: string; value: number; unit: string; source: string }[];
  };
  daily_cash?: CashMonth[] | null;
  cash_projection?: CashProjection | null;
  version: string; company_id: string; group_id: string; month: string; as_of: string;
  health_score: number | null; excluida: boolean; confidence: string; n_meses_ventana: number; n_meses_con_actividad: number;
  c6: number | null; p6: number | null; d6: number | null; t6_efectivo: number | null;
  deficit_servicio_6: number | null; obligacion_vencida_m: number | null;
  r_hist: number | null; colchon_v4: number | null; colchon_aplicable: number | null;
  mora_indice: number | null; multiplicador_deuda: number | null; h_antes_de_ajustes: number | null;
  penalizacion_mora_puntos: number | null; penalizacion_multiplicador_puntos: number | null;
  volumen_ambiguo_eur: number | null; volumen_ambiguo_pct: number | null;
  k: number; alpha: number; beta: number; reasons: string[];
  indice_fx?: number | null; indice_fx_min?: number | null; indice_es_intervalo?: boolean;
  indice_fx_aplicado?: number | null; penalizacion_fx_puntos?: number | null; beta_fx?: number;
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
