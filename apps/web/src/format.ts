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

/** Alert rules in plain words. Fault codes (DTC_xxxx) use the code's own description. */
const ALERT_WORDS: Record<string, string> = {
  COOLANT_OVERHEAT: "Engine overheating",
  COOLANT_DRIFT: "Engine running hotter than normal",
  MISFIRE_ROUGHNESS: "Engine running rough (possible misfire)",
  LV_BATTERY_WEAK: "12 V battery getting weak",
  LV_BATTERY_CRITICAL: "12 V battery critically low",
  TYRE_SLOW_LEAK: "Tyre slowly losing pressure",
  TYRE_PRESSURE_CRITICAL: "Tyre pressure critically low",
  HV_CELL_IMBALANCE: "EV battery cells out of balance",
  HV_CELL_CRITICAL: "EV battery cells critically out of balance",
  VEHICLE_BREAKDOWN: "Vehicle has broken down",
};

const DTC_WORDS: Record<string, string> = {
  P0217: "Engine coolant over temperature",
  P0118: "Coolant temperature sensor reading high",
  P0128: "Thermostat: engine not reaching temperature",
  P0300: "Misfire in several cylinders",
  P0301: "Misfire in cylinder 1",
  P0302: "Misfire in cylinder 2",
  P0303: "Misfire in cylinder 3",
  P0304: "Misfire in cylinder 4",
  P0562: "Electrical system voltage low",
  P0563: "Electrical system voltage high",
  C1A10: "Low tyre pressure, front left",
  C1A11: "Low tyre pressure, front right",
  C1A12: "Low tyre pressure, rear left",
  C1A13: "Low tyre pressure, rear right",
  P0A80: "EV battery pack needs replacing",
  P0A7F: "EV battery pack wearing out",
  P0AFA: "EV battery voltage low",
  P0420: "Catalytic converter below efficiency",
  P0455: "Fuel vapour leak detected",
  U0100: "Lost communication with the engine computer",
};

/** "Engine overheating" for COOLANT_OVERHEAT; "Fault code P0301: Misfire in cylinder 1" for DTC_P0301. */
export function alertLabel(code: string): string {
  if (ALERT_WORDS[code]) return ALERT_WORDS[code];
  if (code.startsWith("DTC_")) {
    const dtc = code.slice(4);
    return DTC_WORDS[dtc] ? `Fault code ${dtc}: ${DTC_WORDS[dtc]}` : `Fault code ${dtc}`;
  }
  return humanize(code);
}

export const dtcLabel = (dtc: string): string => DTC_WORDS[dtc] ?? "Fault code";

/** Shorter form for tight spaces: the description alone (the code is shown on hover). */
export function alertShort(code: string): string {
  const words = code.startsWith("DTC_") ? DTC_WORDS[code.slice(4)] : undefined;
  return words ?? alertLabel(code);
}

const FAILURE_WORDS: Record<string, string> = {
  COOLING_FAILURE: "Engine cooling problem",
  IGNITION_MISFIRE: "Engine misfire",
  LV_BATTERY_FAILURE: "12 V battery / starter problem",
  TYRE_SLOW_LEAK: "Tyre slow leak",
  HV_BATTERY_THERMAL: "EV battery problem",
};

export const failureLabel = (code: string | null | undefined): string =>
  code ? FAILURE_WORDS[code] ?? humanize(code) : "–";

/** Health score (0 to 100, 100 = no problems) as a word a first-time user can act on. */
export function healthWord(score: number | null | undefined): string {
  if (score === null || score === undefined) return "No data yet";
  if (score <= 60) return "Needs attention";
  if (score < 100) return "Keep an eye on";
  return "Healthy";
}

export const roleLabel = (role: string): string =>
  ({ fleet_manager: "Fleet manager", technician: "Technician", analyst: "Analyst", dpo: "Data protection officer",
     platform_admin: "Platform admin" } as Record<string, string>)[role] ?? humanize(role);
