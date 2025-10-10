// src/api.js
import axios from "axios";

// URL dei servizi (override via .env di Vite)
export const COORD_URL = import.meta.env.VITE_COORDINATOR_URL || "http://localhost:8000";
export const LOGIN_URL = import.meta.env.VITE_LOGIN_URL || "http://localhost:7000";

// Istanza HTTP verso il Coordinator (API app)
export const http = axios.create({
  baseURL: COORD_URL,
  headers: { "Content-Type": "application/json" },
});

// Gestione token Bearer comune a tutte le chiamate Coordinator
export function setAuthToken(token) {
  if (token) http.defaults.headers.common["Authorization"] = `Bearer ${token}`;
  else delete http.defaults.headers.common["Authorization"];
}

// =========================
//           AUTH
// =========================
export const DEFAULT_THRESHOLD_SET = {
  delta_kwh_max: 0.08,
  kw_max: 11.0,
  corrente_max: 15.0,
  tensione_max: 232.0,
};
const THRESHOLD_KEYS = Object.keys(DEFAULT_THRESHOLD_SET);

function coerceThresholdSet(src) {
  const base = { ...DEFAULT_THRESHOLD_SET };
  if (!src || typeof src !== "object") return base;
  for (const key of THRESHOLD_KEYS) {
    if (src[key] == null) continue;
    const num = Number(src[key]);
    base[key] = Number.isFinite(num) ? num : base[key];
  }
  return base;
}

function normalizeThresholdsPayload(payload) {
  if (!payload || typeof payload !== "object") {
    return { default: coerceThresholdSet(), per_floor: {} };
  }

  let { default: def, per_floor: perFloor } = payload;
  const looksFlat = def == null && perFloor == null && THRESHOLD_KEYS.every((k) => k in payload);
  if (looksFlat) {
    def = payload;
    perFloor = {};
  }

  const normalizedPerFloor = {};
  if (perFloor && typeof perFloor === "object") {
    for (const [floorKey, set] of Object.entries(perFloor)) {
      normalizedPerFloor[String(floorKey)] = coerceThresholdSet(set);
    }
  }

  return {
    default: coerceThresholdSet(def),
    per_floor: normalizedPerFloor,
  };
}

function prepareThresholdsPayload(config) {
  const normalized = normalizeThresholdsPayload(config);
  const compactPerFloor = {};
  for (const [floor, set] of Object.entries(normalized.per_floor)) {
    const isSameAsDefault = THRESHOLD_KEYS.every((key) => set[key] === normalized.default[key]);
    if (!isSameAsDefault) {
      compactPerFloor[floor] = set;
    }
  }
  return { default: normalized.default, per_floor: compactPerFloor };
}

export async function loginRequest(username, password) {
  const { data } = await axios.post(`${LOGIN_URL}/login`, { username, password });
  return data; // { token }
}

// =========================
/*         ADMIN            */
// =========================
export async function getThresholds() {
  const { data } = await http.get(`/admin/thresholds`);
  return normalizeThresholdsPayload(data);
}
export async function updateThresholds(next) {
  const payload = prepareThresholdsPayload(next);
  const { data } = await http.put(`/admin/thresholds`, payload);
  return normalizeThresholdsPayload(data?.thresholds ?? payload);
}

// =========================
/*     LETTURE (LATEST)     */
// =========================
export async function fetchLatest(floor) {
  const { data } = await http.get(`/key/energy:p${floor}:latest`);
  return data?.value ?? null;
}
export async function fetchLatestAll() {
  const [p1, p2, p3] = await Promise.all([fetchLatest(1), fetchLatest(2), fetchLatest(3)]);
  return { 1: p1, 2: p2, 3: p3 };
}

// =========================
/*       NOTIFICHE (NEW)    */
// =========================

/** Stream realtime di anomalie (SSE).
 *  Uso: const es = createAnomalyEventSource(); es.addEventListener("anomaly", (e) => {...});
 */
export function createAnomalyEventSource() {
  // Nota: EventSource non supporta header custom; l'endpoint SSE deve essere pubblico o autenticato via token nella query.
  return new EventSource(`${COORD_URL}/anomalies/stream`, { withCredentials: false });
}

/** Storico recente notifiche
 *  @returns {Promise<{items: Array, total: number}>}
 */
export async function fetchNotifications(limit = 50, since_ts = null) {
  const params = { limit };
  if (since_ts != null) params.since_ts = since_ts;
  const { data } = await http.get("/notifications", { params });
  return data;
}

/** Numero di notifiche non lette
 *  @returns {Promise<number>}
 */
export async function getUnreadCount() {
  const { data } = await http.get("/notifications/unread_count");
  return data?.unread ?? 0;
}

/** Segna come lette (server-side) le notifiche indicate
 *  @returns {Promise<number>} unread residuo
 */
export async function markNotificationsRead(ids = []) {
  const { data } = await http.post("/notifications/mark_read", { ids });
  return data?.unread ?? 0;
}
