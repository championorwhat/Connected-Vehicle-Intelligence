// Display helpers: consistent, locale-aware formatting in one place.

export const pct = (p: number | null | undefined, digits = 0): string =>
  p === null || p === undefined ? "–" : `${(p * 100).toFixed(digits)}%`;

export const num = (n: number | null | undefined): string =>
  n === null || n === undefined ? "–" : n.toLocaleString();

export function ago(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return "–";
  const s = Math.max(0, Math.round((now - Date.parse(iso)) / 1000));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export const humanize = (code: string | null | undefined): string =>
  code ? code.toLowerCase().replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase()) : "–";

/** Feature names from the model, in words a fleet manager can read. */
const FEATURE_WORDS: Record<string, string> = {
  dtc_rate_short: "fault codes in the last 10 min",
  dtc_rate_long: "fault codes in the last hour",
  lv_min_long: "lowest 12 V battery voltage",
  lv_rest_long: "12 V voltage at rest",
  lv_rest_short: "12 V voltage at rest (recent)",
  tyre_ratio_long: "tyre pressure imbalance",
  tyre_ratio_min_long: "lowest tyre pressure ratio",
  tyre_ratio_min_short: "lowest tyre pressure ratio (recent)",
  tyre_ratio_slope_h: "tyre pressure trend",
  coolant_max_long: "peak coolant temperature",
  coolant_resid_long: "coolant above expected",
  coolant_resid_short: "coolant above expected (recent)",
  coolant_resid_slope_h: "coolant temperature trend",
  rpm_resid_sd_long: "engine roughness",
  rpm_resid_sd_max_long: "worst engine roughness",
  cell_delta_long: "HV cell imbalance",
  cell_delta_max_long: "worst HV cell imbalance",
  cell_delta_slope_h: "HV cell imbalance trend",
  pack_temp_max_long: "peak HV pack temperature",
  soh_long: "HV battery health",
};

export const featureLabel = (f: string): string => FEATURE_WORDS[f] ?? humanize(f);
