import { useEffect, useState } from "react";
import { getThresholds, updateThresholds, COORD_URL } from "../api";

export default function Admin() {
  const [thr, setThr] = useState(null);
  const [msg, setMsg] = useState("");
  const [saving, setSaving] = useState(false);
  const [loading, setLoading] = useState(true);

  async function load() {
    setLoading(true);
    setMsg("");
    try {
      const data = await getThresholds();   // atteso: { delta_kwh_max, kw_max, corrente_max, tensione_max }
      setThr(data);
    } catch (e) {
      console.error("[Admin] GET /admin/thresholds failed:", e);
      setMsg(`Errore caricamento soglie da ${COORD_URL}/admin/thresholds`);
    } finally {
      setLoading(false);
    }
  }

  async function save() {
    if (!thr) return;
    setSaving(true); setMsg("");
    try {
      await updateThresholds(thr);
      setMsg("Soglie salvate ✅");
    } catch (e) {
      console.error("[Admin] PUT /admin/thresholds failed:", e);
      setMsg("Errore salvataggio ❌ (sei loggato? token valido?)");
    } finally {
      setSaving(false);
    }
  }

  useEffect(() => { load(); }, []);

  // Stato loading / errore iniziale
  if (loading) return <div className="card">Caricamento…</div>;
  if (!thr) {
    return (
      <div className="card" style={{ maxWidth: 680, margin: "0 auto" }}>
        <h2 className="h">Soglie Anomalie (Admin)</h2>
        <div className="badge" style={{ marginBottom: 8 }}>{msg || "Impossibile caricare le soglie"}</div>
        <button onClick={load}>Riprova</button>
      </div>
    );
  }

  return (
    <div className="card" style={{ maxWidth: 680, margin: "0 auto" }}>
      <h2 className="h">Soglie Anomalie (Admin)</h2>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(220px, 1fr))", gap: 12 }}>
        <label>ΔkWh max (30s)
          <input
            type="number" step="0.01" value={thr.delta_kwh_max}
            onChange={e => setThr({ ...thr, delta_kwh_max: Number(e.target.value) })}
          />
        </label>
        <label>kW max
          <input
            type="number" step="0.1" value={thr.kw_max}
            onChange={e => setThr({ ...thr, kw_max: Number(e.target.value) })}
          />
        </label>
        <label>Corrente max (A)
          <input
            type="number" step="0.1" value={thr.corrente_max}
            onChange={e => setThr({ ...thr, corrente_max: Number(e.target.value) })}
          />
        </label>
        <label>Tensione max (V)
          <input
            type="number" step="0.1" value={thr.tensione_max}
            onChange={e => setThr({ ...thr, tensione_max: Number(e.target.value) })}
          />
        </label>
      </div>

      <div style={{ marginTop: 12, display: "flex", gap: 8, alignItems: "center" }}>
        <button onClick={save} disabled={saving}>{saving ? "Salvataggio..." : "Salva"}</button>
        {msg && <span className="badge">{msg}</span>}
      </div>
    </div>
  );
}