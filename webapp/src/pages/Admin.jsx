import { useEffect, useState } from "react";
import { getThresholds, updateThresholds, COORD_URL } from "../api";

const FIELDS = [
  { key: "delta_kwh_max", label: "ΔkWh max (30s)", step: 0.01 },
  { key: "kw_max", label: "kW max", step: 0.1 },
  { key: "corrente_max", label: "Corrente max (A)", step: 0.1 },
  { key: "tensione_max", label: "Tensione max (V)", step: 0.1 },
];

const FLOORS = [1, 2, 3];

const CARD_GRID_STYLE = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))",
  gap: 16,
  alignItems: "stretch",
};

const FIELD_LABEL_STYLE = {
  display: "flex",
  flexDirection: "column",
  gap: 6,
};

function clonePerFloor(map) {
  const out = {};
  if (!map) return out;
  for (const [key, value] of Object.entries(map)) {
    out[key] = { ...value };
  }
  return out;
}

export default function Admin() {
  const [cfg, setCfg] = useState(null);
  const [msg, setMsg] = useState("");
  const [saving, setSaving] = useState(false);
  const [loading, setLoading] = useState(true);

  async function load() {
    setLoading(true);
    setMsg("");
    try {
      const data = await getThresholds();
      setCfg(data);
    } catch (e) {
      console.error("[Admin] GET /admin/thresholds failed:", e);
      setMsg("Errore caricamento soglie da " + COORD_URL + "/admin/thresholds");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  function updateScope(scope, field, rawValue) {
    setCfg((prev) => {
      if (!prev) return prev;
      const value = Number(rawValue);
      const safe = Number.isFinite(value) ? value : 0;
      const nextDefault = { ...prev.default };
      const nextPerFloor = clonePerFloor(prev.per_floor);

      if (scope === "default") {
        nextDefault[field] = safe;
      } else {
        const floorKey = String(scope);
        const base = nextPerFloor[floorKey] ? { ...nextPerFloor[floorKey] } : { ...nextDefault };
        base[field] = safe;
        nextPerFloor[floorKey] = base;
      }

      return { default: nextDefault, per_floor: nextPerFloor };
    });
  }

  function resetFloor(floor) {
    setCfg((prev) => {
      if (!prev) return prev;
      const floorKey = String(floor);
      if (!prev.per_floor || !(floorKey in prev.per_floor)) return prev;
      const nextPerFloor = clonePerFloor(prev.per_floor);
      delete nextPerFloor[floorKey];
      return { default: { ...prev.default }, per_floor: nextPerFloor };
    });
  }

  async function save() {
    if (!cfg) return;
    setSaving(true);
    setMsg("");
    try {
      const updated = await updateThresholds(cfg);
      setCfg(updated);
      setMsg("Soglie salvate ✅");
    } catch (e) {
      console.error("[Admin] PUT /admin/thresholds failed:", e);
      setMsg("Errore salvataggio ❌ (sei loggato? token valido?)");
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return <div className="card">Caricamento…</div>;
  }

  if (!cfg) {
    return (
      <div className="card" style={{ maxWidth: 680, margin: "0 auto" }}>
        <h2 className="h">Soglie Anomalie (Admin)</h2>
        <div className="badge" style={{ marginBottom: 8 }}>{msg || "Impossibile caricare le soglie"}</div>
        <button onClick={load}>Riprova</button>
      </div>
    );
  }

  const scopes = [
    { id: "default", label: "Valori base (fallback)" },
    ...FLOORS.map((floor) => ({ id: floor, label: "Piano " + floor })),
  ];

  return (
    <div className="card" style={{ maxWidth: 820, margin: "0 auto" }}>
      <h2 className="h">Soglie Anomalie (Admin)</h2>
      <p className="label" style={{ opacity: 0.7, marginBottom: 16 }}>
        Ogni piano eredita i valori base, ma puoi impostare soglie dedicate quando necessario.
      </p>

      <div style={{ display: "grid", gap: 16 }}>
        {scopes.map((scope) => {
          const floorKey = String(scope.id);
          const override = scope.id === "default" ? null : cfg.per_floor?.[floorKey] ?? null;
          const values = scope.id === "default" ? cfg.default : override || cfg.default;
          const inherits = scope.id !== "default" && !override;

          return (
            <div key={floorKey} className="card" style={{ padding: 16, background: "#0f1934", border: "1px solid #1f2a4a" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 12 }}>
                <h3 className="h" style={{ margin: 0 }}>{scope.label}</h3>
                {inherits && (
                  <span className="badge" style={{ background: "#243357", opacity: 0.85 }}>Eredita default</span>
                )}
                {!inherits && scope.id !== "default" && (
                  <button className="badge" onClick={() => resetFloor(scope.id)}>Ripristina default</button>
                )}
              </div>

              <div style={CARD_GRID_STYLE}>
                {FIELDS.map((field) => (
                  <label key={field.key} style={FIELD_LABEL_STYLE}>
                    <span>{field.label}</span>
                    <input
                      type="number"
                      min={0}
                      step={field.step}
                      value={values[field.key] ?? ""}
                      onChange={(e) => updateScope(scope.id, field.key, e.target.value)}
                      style={{ width: "100%" }}
                    />
                  </label>
                ))}
              </div>
            </div>
          );
        })}
      </div>

      <div style={{ marginTop: 16, display: "flex", gap: 8, alignItems: "center" }}>
        <button onClick={save} disabled={saving}>{saving ? "Salvataggio..." : "Salva"}</button>
        {msg && <span className="badge">{msg}</span>}
      </div>
    </div>
  );
}
