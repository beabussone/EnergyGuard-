import { useEffect, useRef, useState } from "react";
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
  Scatter, ScatterChart,
} from "recharts";
import Login from "./pages/Login";
import Admin from "./pages/Admin";
import { fetchLatestAll, setAuthToken } from "./api";

const POLL_MS = 30_000;   // refresh ogni 30s
const MAX_POINTS = 240;   // ~2h a 30s/campione

// --- auth state (token in localStorage) ---
function useAuthState() {
  const [token, setToken] = useState(() => localStorage.getItem("eg_token") || "");
  useEffect(() => {
    if (token) setAuthToken(token);
    else setAuthToken(null);
  }, [token]);
  const login = (t) => { localStorage.setItem("eg_token", t); setToken(t); };
  const logout = () => { localStorage.removeItem("eg_token"); setToken(""); };
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

// --- Dashboard ---
function Dashboard() {
  // serie unificate nel tempo; ogni row = un timestamp
  const [series, setSeries] = useState([]); // { t, label, p1_kw, p2_kw, p3_kw, p1_a, ... p3_v, p1_dkwh, ... }
  const [latest, setLatest] = useState({ 1: null, 2: null, 3: null });
  const [metric, setMetric] = useState("kw"); // "kw" | "a" | "v" | "dkwh"
  const lastKwhRef = useRef({}); // per calcolare ΔkWh per piano
  const timer = useRef(null);

  async function pull() {
    try {
      const all = await fetchLatestAll(); // {1:{...},2:{...},3:{...}}
      if (!all[1] || !all[2] || !all[3]) return;

      // calcola delta kWh per piano (solo positivo)
      const dk = {};
      [1,2,3].forEach(p => {
        const k = Number(all[p]?.sensore1_kwh ?? 0);
        const prev = Number(lastKwhRef.current[p] ?? k);
        const delta = Math.max(0, k - prev);
        dk[p] = delta;
        lastKwhRef.current[p] = k;
      });

      const t = all[1].timestamp || all[2].timestamp || all[3].timestamp;
      const row = {
        t,
        label: formatTime(t),
        // kW
        p1_kw: all[1]?.sensore2_kw ?? null,
        p2_kw: all[2]?.sensore2_kw ?? null,
        p3_kw: all[3]?.sensore2_kw ?? null,
        // A
        p1_a: all[1]?.sensore3_corrente ?? null,
        p2_a: all[2]?.sensore3_corrente ?? null,
        p3_a: all[3]?.sensore3_corrente ?? null,
        // V
        p1_v: all[1]?.sensore3_tensione ?? null,
        p2_v: all[2]?.sensore3_tensione ?? null,
        p3_v: all[3]?.sensore3_tensione ?? null,
        // ΔkWh
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
    } catch (e) {
      // nessun log rumoroso; alla prima esecuzione può non esserci nulla
    }
  }

  useEffect(() => {
    pull(); // primo fetch immediato
    timer.current = setInterval(pull, POLL_MS);
    return () => clearInterval(timer.current);
  }, []);

  const hasData = series.length > 0;

  // mapping metric -> dataKeys
  const metricConfig = {
    kw:   { title: "Potenza istantanea (kW)",   keys: ["p1_kw","p2_kw","p3_kw"], yLabel: "kW" },
    a:    { title: "Corrente (A)",              keys: ["p1_a","p2_a","p3_a"],   yLabel: "A"  },
    v:    { title: "Tensione (V)",              keys: ["p1_v","p2_v","p3_v"],   yLabel: "V"  },
    dkwh: { title: "Incremento energia ΔkWh",   keys: ["p1_dkwh","p2_dkwh","p3_dkwh"], yLabel: "ΔkWh" },
  };
  const cfg = metricConfig[metric];

  return (
    <div className="card">
      <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
        <h2 className="h" style={{ margin: 0 }}>Dashboard Live</h2>
        <label style={{ marginLeft: "auto" }}>
          Grandezza:&nbsp;
          <select value={metric} onChange={e => setMetric(e.target.value)}>
            <option value="kw">kW (potenza)</option>
            <option value="a">A (corrente)</option>
            <option value="v">V (tensione)</option>
            <option value="dkwh">ΔkWh (incremento 30s)</option>
          </select>
        </label>
      </div>

      {!hasData && <div className="badge" style={{ marginTop: 8 }}>In attesa di nuove letture…</div>}

      {hasData && (
        <>
          {/* Linee + pallini e legenda (un dataset per piano) */}
          <section style={{ height: 360, marginTop: 12 }}>
            <h3 className="h" style={{ marginBottom: 8 }}>{cfg.title}</h3>
            <ResponsiveContainer width="100%" height="100%">
  <LineChart data={series}>
    <CartesianGrid strokeDasharray="3 3" />
    <XAxis dataKey="label" interval="preserveEnd" minTickGap={24} />
    <YAxis />
    <Tooltip />
    <Legend />
    <Line
      type="monotone"
      dataKey={cfg.keys[0]}
      name="Piano 1"
      stroke="#007bff"
      dot={{ stroke: "#007bff", fill: "#007bff" }}
    />
    <Line
      type="monotone"
      dataKey={cfg.keys[1]}
      name="Piano 2"
      stroke="#dc3545"
      dot={{ stroke: "#dc3545", fill: "#dc3545" }}
    />
    <Line
      type="monotone"
      dataKey={cfg.keys[2]}
      name="Piano 3"
      stroke="#28a745"
      dot={{ stroke: "#28a745", fill: "#28a745" }}
    />
  </LineChart>
</ResponsiveContainer>
          </section>

          {/* Ultime misurazioni per piano */}
          <section style={{ marginTop: 16 }}>
            <h3 className="h" style={{ marginBottom: 8 }}>Ultime misurazioni</h3>
            <div style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))",
              gap: 12
            }}>
              {[1,2,3].map(p => {
                const r = latest[p];
                return (
                  <div key={p} className="card" style={{ borderLeft: `6px solid var(--c${p}, #888)` }}>
                    <div className="kpi" style={{ marginBottom: 8, gap: 8 }}>
                      <div>
                        <div className="label">Piano</div>
                        <div className="value">P{p}</div>
                      </div>
                      <div>
                        <div className="label">kW</div>
                        <div className="value">{r?.sensore2_kw ?? "-"}</div>
                      </div>
                      <div>
                        <div className="label">A</div>
                        <div className="value">{r?.sensore3_corrente ?? "-"}</div>
                      </div>
                      <div>
                        <div className="label">V</div>
                        <div className="value">{r?.sensore3_tensione ?? "-"}</div>
                      </div>
                      <div>
                        <div className="label">kWh</div>
                        <div className="value">{r?.sensore1_kwh ?? "-"}</div>
                      </div>
                    </div>
                    <div className="label">Timestamp</div>
                    <div>{r?.timestamp ? new Date(r.timestamp).toLocaleString() : "-"}</div>
                  </div>
                );
              })}
            </div>
          </section>
        </>
      )}
    </div>
  );
}

// --- Navbar + Router root ---
function NavBar({ logged, onLogout }) {
  const loc = useLocation();
  const navigate = useNavigate();
  const here = (p) => (loc.pathname === p ? { fontWeight: 700 } : undefined);

  return (
    <div className="card" style={{ display: "flex", alignItems: "center", gap: 12 }}>
      <Link to="/" style={here("/")}>Dashboard</Link>
      <span>•</span>
      <Link to="/admin" style={here("/admin")}>Console Admin</Link>
      <div style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
        {logged ? (
          <button onClick={() => { onLogout(); navigate("/"); }}>Logout</button>
        ) : (
          <Link to="/admin" style={here("/admin")}>Console Admin</Link>
        )}
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
         <Route
  path="/login"
  element={
    <Login
      onLogged={(token) => {
        // aggiorna lo stato globale di auth
        auth.login(token);            // <-- imposta token in stato + setAuthToken + localStorage
        // vai alla pagina Admin
        window.location.hash = "#/admin";
        // (in alternativa, se usi useNavigate qui: navigate("/admin"))
      }}
    />
  }
/>
          <Route
            path="/admin"
            element={
              <ProtectedRoute logged={auth.logged}>
                <Admin />
              </ProtectedRoute>
            }
          />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Router>
    </div>
  );
}