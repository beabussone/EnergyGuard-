// src/App.jsx
import { useEffect, useMemo, useRef, useState } from "react";
import {
  HashRouter as Router,
  Routes,
  Route,
  Navigate,
  Link,
  useLocation,
  useNavigate,
} from "react-router-dom";
import {
  LineChart, Line, CartesianGrid, XAxis, YAxis, Tooltip, Legend, ResponsiveContainer,
} from "recharts";
import Login from "./pages/Login";
import Admin from "./pages/Admin";
import {
  fetchLatestAll,
  setAuthToken,
  COORD_URL,
  fetchNotifications,
  getUnreadCount,
  markNotificationsRead,
  getThresholds,
} from "./api";

const POLL_MS = 5_000;    // refresh ogni 5s
const MAX_POINTS = 1_440; // ~2h a 5s/campione

// --- auth state ---
function useAuthState() {
  const [token, setToken] = useState(() => sessionStorage.getItem("eg_token") || "");
  useEffect(() => { localStorage.removeItem("eg_token"); }, []);
  useEffect(() => { token ? setAuthToken(token) : setAuthToken(null); }, [token]);
  const login = (t) => { sessionStorage.setItem("eg_token", t); setToken(t); };
  const logout = () => { sessionStorage.removeItem("eg_token"); localStorage.removeItem("eg_token"); setToken(""); };
  return { token, logged: !!token, login, logout };
}

function ProtectedRoute({ logged, children }) {
  const loc = useLocation();
  if (!logged) return <Navigate to="/login" state={{ from: loc }} replace />;
  return children;
}

function formatTime(ts) {
  try {
    const d = new Date(ts);
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  } catch { return ts; }
}

// --- Helper timestamp: usa timestamp_ms se presente ---
// === INIZIO PATCH: helper per timestamp ===
function tsToLocal(ts, tsMs) {
  const toLocale = (ms) => new Date(ms).toLocaleString();

  if (tsMs != null) {
    const ms = Number(tsMs);
    if (Number.isFinite(ms) && ms > 0) {
      return toLocale(ms);
    }
  }

  if (ts != null) {
    const seconds = Number(ts);
    if (Number.isFinite(seconds) && seconds > 0) {
      return toLocale(seconds * 1000);
    }
    if (typeof ts === "string") {
      const parsed = Date.parse(ts);
      if (!Number.isNaN(parsed)) {
        return toLocale(parsed);
      }
    }
  }

  return "";
}
// === FINE PATCH ===


// --- Dashboard ---
function Dashboard() {
  const [series, setSeries] = useState([]);
  const [latest, setLatest] = useState({ 1: null, 2: null, 3: null });
  const [metric, setMetric] = useState("kw");
  const [historyCount, setHistoryCount] = useState(1);
  const lastKwhRef = useRef({});
  const lastTimestampRef = useRef(null);
  const timer = useRef(null);
  const [thresholds, setThresholds] = useState(null);

  const [notifications, setNotifications] = useState([]);
  const [unread, setUnread] = useState(0);
  const [toast, setToast] = useState(null);
  const [bellOpen, setBellOpen] = useState(false);
  const seenIdsRef = useRef(new Set());

  async function pull() {
    try {
      const all = await fetchLatestAll();
      if (!all[1] || !all[2] || !all[3]) return;

      const primary = all[1] ?? all[2] ?? all[3];
      const tsMs = primary?.timestamp_ms ?? all[2]?.timestamp_ms ?? all[3]?.timestamp_ms;
      const tsIso = primary?.timestamp ?? all[2]?.timestamp ?? all[3]?.timestamp ?? null;
      const msKey = tsMs != null ? Number(tsMs) : null;
      const stampKey = Number.isFinite(msKey) && msKey > 0 ? msKey : tsIso;

      if (!stampKey) return;
      if (lastTimestampRef.current != null && lastTimestampRef.current === stampKey) {
        return; // nessun aggiornamento nuovo dal coordinator
      }

      const nextKwhState = { ...lastKwhRef.current };
      const dk = {};
      [1,2,3].forEach(p => {
        const k = Number(all[p]?.sensore1_kwh ?? 0);
        const prev = Number(nextKwhState[p] ?? k);
        const delta = Math.max(0, k - prev);
        dk[p] = delta;
        nextKwhState[p] = k;
      });

      const canonicalTs = tsIso ?? (Number.isFinite(msKey) ? new Date(msKey).toISOString() : null);
      const labelSource = canonicalTs ?? "";
      const row = {
        t: canonicalTs,
        label: formatTime(labelSource),
        p1_kw: all[1]?.sensore2_kw ?? null,
        p2_kw: all[2]?.sensore2_kw ?? null,
        p3_kw: all[3]?.sensore2_kw ?? null,
        p1_a: all[1]?.sensore3_corrente ?? null,
        p2_a: all[2]?.sensore3_corrente ?? null,
        p3_a: all[3]?.sensore3_corrente ?? null,
        p1_v: all[1]?.sensore3_tensione ?? null,
        p2_v: all[2]?.sensore3_tensione ?? null,
        p3_v: all[3]?.sensore3_tensione ?? null,
        p1_dkwh: dk[1],
        p2_dkwh: dk[2],
        p3_dkwh: dk[3],
      };

      setLatest(all);
      setSeries(prev => {
        const next = [...prev, row];
        if (next.length > MAX_POINTS) next.shift();
        return next;
      });
      lastTimestampRef.current = stampKey;
      lastKwhRef.current = nextKwhState;
    } catch {}
  }

  // --- SSE + init notifiche ---
  useEffect(() => {
    (async () => {
      try {
        const [hist, cnt] = await Promise.all([fetchNotifications(20), getUnreadCount()]);
        const items = (hist?.items || []).slice(-20);
        items.forEach(it => seenIdsRef.current.add(it.id));
        setNotifications(items.reverse());
        setUnread(cnt);
      } catch {}
    })();

    const es = new EventSource(`${COORD_URL}/anomalies/stream`, { withCredentials: false });

    es.addEventListener("anomaly", (e) => {
      try {
        const a = JSON.parse(e.data);
        if (!a?.id) return;
        if (seenIdsRef.current.has(a.id)) return;
        seenIdsRef.current.add(a.id);

        setNotifications((prev) => [{ ...a }, ...prev].slice(0, 50));
        setUnread((u) => u + 1);
        setToast({
          title: `⚠️ Anomalia su P${a.floor} (${a.metric})`,
          text: a.message || `Valore ${a.value} > soglia ${a.threshold}`,
          at: a.timestamp,          // seconds
          at_ms: a.timestamp_ms,    // milliseconds (preferito)
          severity: a.severity || "warning",
        });

        setTimeout(() => setToast(null), 8000);
      } catch {}
    });

    es.addEventListener("ping", () => {});
    return () => es.close();
  }, []);

  useEffect(() => {
    pull();
    timer.current = setInterval(pull, POLL_MS);
    return () => clearInterval(timer.current);
  }, []);

  useEffect(() => {
    (async () => {
      try {
        const data = await getThresholds();
        setThresholds(data);
      } catch {}
    })();
  }, []);

  async function markAllRead() {
    try {
      const ids = notifications.map(n => n.id);
      const newUnread = await markNotificationsRead(ids);
      setUnread(newUnread);
    } catch { setUnread(0); }
  }

  function sevColor(sev) {
    switch (sev) {
      case "critical": return "#b00020";
      case "high":     return "#d33";
      case "warning":  return "#d9822b";
      default:         return "#3366cc";
    }
  }

  const FLOORS = [1, 2, 3];
  const COLORS = { 1: "#3a7bd5", 2: "#a944ff", 3: "#42d392" };
  const latestRow = series.length > 0 ? series[series.length - 1] : null;

  const toNumber = (value) => {
    if (value == null) return null;
    const num = Number(value);
    return Number.isFinite(num) ? num : null;
  };

  const thresholdsMatrix = useMemo(() => {
    if (!thresholds) return {};
    const def = thresholds.default || {};
    const perFloor = thresholds.per_floor || {};
    const extract = (src) => ({
      kw: toNumber((src && src.kw_max) ?? def.kw_max),
      a: toNumber((src && src.corrente_max) ?? def.corrente_max),
      v: toNumber((src && src.tensione_max) ?? def.tensione_max),
      dkwh: toNumber((src && src.delta_kwh_max) ?? def.delta_kwh_max),
    });
    const matrix = {};
    FLOORS.forEach((floor) => {
      const key = String(floor);
      const specific = perFloor[key] ?? perFloor[floor];
      matrix[floor] = extract(specific || def);
    });
    return matrix;
  }, [thresholds]);

  const METRICS = {
    kw: {
      label: "Potenza (kW)",
      unit: "kW",
      precision: 2,
      aggregate: true,
      dataKeys: { 1: "p1_kw", 2: "p2_kw", 3: "p3_kw" },
      latestValue: (floor) => toNumber(latest[floor]?.sensore2_kw),
    },
    a: {
      label: "Corrente (A)",
      unit: "A",
      precision: 2,
      aggregate: false,
      dataKeys: { 1: "p1_a", 2: "p2_a", 3: "p3_a" },
      latestValue: (floor) => toNumber(latest[floor]?.sensore3_corrente),
    },
    v: {
      label: "Tensione (V)",
      unit: "V",
      precision: 1,
      aggregate: false,
      dataKeys: { 1: "p1_v", 2: "p2_v", 3: "p3_v" },
      latestValue: (floor) => toNumber(latest[floor]?.sensore3_tensione),
    },
    dkwh: {
      label: "Energia delta (kWh)",
      unit: "kWh",
      precision: 3,
      aggregate: true,
      dataKeys: { 1: "p1_dkwh", 2: "p2_dkwh", 3: "p3_dkwh" },
      latestValue: (floor) => toNumber(latestRow?.[`p${floor}_dkwh`]),
    },
  };

  const activeMetric = METRICS[metric] ?? METRICS.kw;
  const METRIC_ORDER = ["kw", "a", "v", "dkwh"];
  const AGG_KEYS = METRIC_ORDER.filter((key) => METRICS[key].aggregate);

  const historyLimit = historyCount > 0 ? historyCount : 1;
  const historyRows = historyLimit > 0 ? series.slice(-historyLimit) : [];
  const historyRowsDesc = historyRows.length > 0 ? [...historyRows].reverse() : [];

  const formatDisplay = (value, metricDef = activeMetric) => {
    const num = toNumber(value);
    if (num == null) return "—";
    const abs = Math.abs(num);
    const digits = metricDef.precision ?? 2;
    const decimals = abs >= 100 ? 0 : abs >= 10 ? Math.min(1, digits) : digits;
    return `${num.toFixed(decimals)} ${metricDef.unit}`;
  };

  const aggregatedHistory = historyRowsDesc.map((row) => {
    const totals = {};
    METRIC_ORDER.forEach((key) => {
      const def = METRICS[key];
      if (!def.aggregate) return;
      let sum = 0;
      let has = false;
      FLOORS.forEach((floor) => {
        const dataKey = def.dataKeys[floor];
        if (!dataKey) return;
        const value = toNumber(row[dataKey]);
        if (value != null) {
          sum += value;
          has = true;
        }
      });
      totals[key] = has ? sum : null;
    });
    return { row, totals };
  });

  const lastSample = (() => {
    for (const floor of FLOORS) {
      const sample = latest[floor];
      if (sample?.timestamp_ms || sample?.timestamp) return sample;
    }
    if (latestRow?.t) return { timestamp: latestRow.t };
    return null;
  })();

  let lastUpdate = "—";
  if (lastSample) {
    const human = tsToLocal(lastSample.timestamp, lastSample.timestamp_ms);
    if (human) {
      lastUpdate = human;
    } else if (lastSample.timestamp) {
      lastUpdate = formatTime(lastSample.timestamp);
    }
  } else if (latestRow?.label) {
    lastUpdate = latestRow.label;
  }

  return (
    <div className="card">
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <h2 className="h">Dashboard Live</h2>

        <div style={{ marginLeft: "auto", position: "relative" }}>
          <button className="badge" onClick={() => setBellOpen(v => !v)}>
            🔔 Notifiche
            {unread > 0 && (
              <span style={{
                position: "absolute", top: -8, right: -8,
                background: "#d33", color: "#fff",
                borderRadius: 12, padding: "2px 6px", fontSize: 12
              }}>{unread}</span>
            )}
          </button>

          {bellOpen && (
            <div
              className="card"
              style={{
                position: "absolute",
                right: 0,
                top: "calc(100% + 8px)",
                width: 360,
                maxHeight: 360,
                overflow: "auto",
                background: "#101a36",
                boxShadow: "0 12px 30px rgba(0, 0, 0, 0.6)",
                border: "1px solid #1f2a4a",
                zIndex: 20,
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <div className="h">Notifiche</div>
                <button onClick={markAllRead}>Segna tutto letto</button>
              </div>
              {notifications.length === 0 && <div className="label">Nessuna notifica</div>}
              {notifications.map(n => (
                <div key={n.id} style={{ borderLeft: `6px solid ${sevColor(n.severity)}`, paddingLeft: 8, marginBottom: 10 }}>
                  <div className="label" style={{ opacity: 0.8 }}>
                    {tsToLocal(n.timestamp, n.timestamp_ms)}
                </div>
                  <div style={{ fontWeight: 600 }}>
                    P{n.floor} — {n.metric} ({n.value} / soglia {n.threshold})
                  </div>
                  <div>{n.message}</div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      <div className="controls">
        <label htmlFor="metric">Metrica</label>
        <select id="metric" value={metric} onChange={(e) => setMetric(e.target.value)}>
          <option value="kw">Potenza (kW)</option>
          <option value="a">Corrente (A)</option>
          <option value="v">Tensione (V)</option>
          <option value="dkwh">Energia delta (kWh)</option>
        </select>
        <label htmlFor="history" style={{ marginLeft: 12 }}>Campioni</label>
        <select
          id="history"
          value={historyLimit}
          onChange={(e) => setHistoryCount(Math.max(1, Number(e.target.value) || 1))}
        >
          <option value={1}>Ultimo</option>
          <option value={3}>Ultimi 3</option>
          <option value={5}>Ultimi 5</option>
          <option value={10}>Ultimi 10</option>
        </select>
        <div className="label" style={{ marginLeft: "auto", opacity: 0.75 }}>
          Ultimo aggiornamento: {lastUpdate}
        </div>
      </div>

      <div
        style={{
          width: "100%",
          height: 320,
          background: "#0e162c",
          borderRadius: 12,
          border: "1px solid #1a2444",
          padding: 12,
        }}
      >
        {series.length === 0 ? (
          <div className="label" style={{ textAlign: "center", marginTop: 120 }}>
            In attesa di dati dal coordinatore…
          </div>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={series} margin={{ top: 12, right: 24, left: 0, bottom: 12 }}>
              <CartesianGrid stroke="#18244a" strokeDasharray="4 4" />
              <XAxis dataKey="label" stroke="#6b7aa7" minTickGap={20} />
              <YAxis stroke="#6b7aa7" width={70} />
              <Tooltip
                cursor={{ strokeDasharray: "3 3", stroke: "#6b7aa7" }}
                contentStyle={{ background: "#0e162c", border: "1px solid #1a2444", borderRadius: 8 }}
                formatter={(value) => formatDisplay(value)}
                labelFormatter={(label) => `Ora ${label}`}
              />
              <Legend />
              {FLOORS.map((floor) => {
                const dataKey = activeMetric.dataKeys[floor];
                if (!dataKey) return null;
                return (
                  <Line
                    key={dataKey}
                    type="monotone"
                    dataKey={dataKey}
                    stroke={COLORS[floor]}
                    strokeWidth={2}
                    dot={false}
                    connectNulls
                    name={`P${floor}`}
                  />
                );
              })}
            </LineChart>
          </ResponsiveContainer>
        )}
      </div>

      <div
        className="kpi"
        style={{
          marginTop: 16,
          display: "grid",
          gap: 12,
          gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
        }}
      >
        {FLOORS.map((floor) => (
          <div key={floor}>
            <div className="label" style={{ opacity: 0.75, fontWeight: 600 }}>P{floor}</div>
            {historyRowsDesc.length === 0 ? (
              <div className="label" style={{ marginTop: 8, opacity: 0.6 }}>Nessuna lettura</div>
            ) : (
              historyRowsDesc.map((row, idx) => {
                const rowLabel = row.label || `Campione ${idx + 1}`;
                const rowKey = row.t != null ? `${row.t}-${idx}` : `${rowLabel}-${idx}`;
                return (
                  <div
                    key={rowKey}
                    style={{
                      marginTop: 8,
                      padding: "8px 0",
                      borderBottom: "1px solid rgba(255,255,255,0.08)",
                    }}
                  >
                    <div className="label" style={{ opacity: 0.65 }}>{rowLabel}</div>
                    {METRIC_ORDER.map((key) => {
                      const def = METRICS[key];
                      const dataKey = def.dataKeys[floor];
                      if (!dataKey) return null;
                      const value = toNumber(row[dataKey]);
                      const display = formatDisplay(value, def);
                      const threshold = thresholdsMatrix[floor]?.[key];
                      const above = threshold != null && value != null && value > threshold;
                      return (
                        <div
                          key={`${rowKey}-${key}`}
                          style={{
                            display: "flex",
                            justifyContent: "space-between",
                            alignItems: "center",
                            marginTop: 4,
                          }}
                        >
                          <span className="label" style={{ opacity: 0.75 }}>{def.label}</span>
                          <span style={{ fontWeight: 600, color: above ? "#ff4d4d" : undefined }}>
                            {display}
                          </span>
                        </div>
                      );
                    })}
                  </div>
                );
              })
            )}
          </div>
        ))}
        {aggregatedHistory.some(({ totals }) =>
          AGG_KEYS.some((key) => totals[key] != null)
        ) && (
          <div>
            <div className="label" style={{ opacity: 0.75, fontWeight: 600 }}>Totale (somme multi-piano)</div>
            {aggregatedHistory.map(({ row, totals }, idx) => {
              const rowLabel = row.label || `Campione ${idx + 1}`;
              const groupKey = row.t != null ? `${row.t}-agg-${idx}` : `${rowLabel}-agg-${idx}`;
              if (!AGG_KEYS.some((key) => totals[key] != null)) return null;
              return (
                <div
                  key={groupKey}
                  style={{
                    marginTop: 8,
                    padding: "8px 0",
                    borderBottom: "1px solid rgba(255,255,255,0.08)",
                  }}
                >
                  <div className="label" style={{ opacity: 0.65 }}>{rowLabel}</div>
                  {AGG_KEYS.map((key) => {
                    const def = METRICS[key];
                    const total = totals[key];
                    if (total == null) return null;
                    return (
                      <div
                        key={`${groupKey}-${key}`}
                        style={{
                          display: "flex",
                          justifyContent: "space-between",
                          alignItems: "center",
                          marginTop: 4,
                        }}
                      >
                        <span className="label" style={{ opacity: 0.75 }}>{def.label}</span>
                        <span style={{ fontWeight: 600 }}>{formatDisplay(total, def)}</span>
                      </div>
                    );
                  })}
                </div>
              );
            })}
          </div>
        )}
      </div>
      {toast && (
        <div className="card" style={{ position: "fixed", right: 16, bottom: 16, zIndex: 1000, borderLeft: `6px solid ${sevColor(toast.severity)}` }}>
          <div className="h">{toast.title}</div>
          <div className="label" style={{ opacity: 0.8 }}>
            {tsToLocal(toast.at, toast.at_ms)}
          </div>
          <div>{toast.text}</div>
          <button onClick={() => setToast(null)}>Chiudi</button>
        </div>
      )}
    </div>
  );
}

function NavBar({ logged, onLogout }) {
  const loc = useLocation();
  const navigate = useNavigate();
  const here = (p) => (loc.pathname === p ? { fontWeight: 700 } : undefined);

  return (
    <div className="card" style={{ display: "flex", gap: 8 }}>
      <Link to="/" style={here("/")}>Dashboard</Link>
      <Link to="/admin" style={here("/admin")}>Console Admin</Link>
      <div style={{ marginLeft: "auto" }}>
        {logged ? <button onClick={() => { onLogout(); navigate("/"); }}>Logout</button> : <Link to="/login">Login</Link>}
      </div>
    </div>
  );
}

export default function App() {
  const auth = useAuthState();
  return (
    <div className="container">
      <h1 className="h">EnergyGuard – Dashboard & Admin</h1>
      <Router>
        <NavBar logged={auth.logged} onLogout={auth.logout} />
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/login" element={<Login onLogged={auth.login} />} />
          <Route path="/admin" element={<ProtectedRoute logged={auth.logged}><Admin /></ProtectedRoute>} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Router>
    </div>
  );
}
