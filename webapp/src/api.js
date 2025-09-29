import axios from "axios";

export const COORD_URL = import.meta.env.VITE_COORDINATOR_URL || "http://localhost:8000";
export const LOGIN_URL = import.meta.env.VITE_LOGIN_URL || "http://localhost:7000";

export const http = axios.create({
  baseURL: COORD_URL,
  headers: { "Content-Type": "application/json" },
});

export function setAuthToken(token) {
  if (token) http.defaults.headers.common["Authorization"] = `Bearer ${token}`;
  else delete http.defaults.headers.common["Authorization"];
}

// Auth
export async function loginRequest(username, password) {
  const { data } = await axios.post(`${LOGIN_URL}/login`, { username, password });
  return data; // { token }
}

// Thresholds
export async function getThresholds() {
  const { data } = await http.get(`/admin/thresholds`);
  return data;
}
export async function updateThresholds(next) {
  const { data } = await http.put(`/admin/thresholds`, next);
  return data;
}

// Letture (latest)
export async function fetchLatest(floor) {
  const { data } = await http.get(`/key/energy:p${floor}:latest`);
  return data?.value ?? null;
}
export async function fetchLatestAll() {
  const [p1,p2,p3] = await Promise.all([fetchLatest(1), fetchLatest(2), fetchLatest(3)]);
  return {1:p1,2:p2,3:p3};
}