import { useState } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import { loginRequest, setAuthToken } from "../api";

export default function Login({ onLogged }) {
  const [username, setU] = useState("");
  const [password, setP] = useState("");
  const [err, setErr] = useState("");

  const navigate = useNavigate();
  const location = useLocation();

  async function submit(e) {
    e.preventDefault();
    setErr("");
    try {
      const res = await loginRequest(username, password); // { token }
      if (!res?.token) {
        setErr("Login: risposta senza token");
        return;
      }

      // salva e attiva il token per le chiamate axios
      localStorage.setItem("eg_token", res.token);
      setAuthToken(res.token);

      // NOTIFICA il parent con il token (così aggiorna lo stato logged)
      onLogged?.(res.token);

      // naviga alla destinazione protetta o a /admin
      const dest = location.state?.from?.pathname || "/admin";
      navigate(dest, { replace: true });
    } catch (e) {
      const msg =
        (e.response && (e.response.data?.detail || e.response.statusText)) ||
        e.message ||
        "Errore di rete/CORS";
      setErr(`Credenziali non valide o errore: ${msg}`);
    }
  }

  return (
    <div className="card" style={{ maxWidth: 360, margin: "40px auto" }}>
      <h2 className="h">Login Amministratore</h2>
      <form onSubmit={submit} className="kpi" style={{ gap: 8 }}>
        <input
          placeholder="Username"
          value={username}
          onChange={(e) => setU(e.target.value)}
        />
        <input
          placeholder="Password"
          type="password"
          value={password}
          onChange={(e) => setP(e.target.value)}
        />
        <button type="submit">Entra</button>
        {err && <div className="badge" style={{ background: "#fdd" }}>{err}</div>}
      </form>
      <div className="label" style={{ marginTop: 8 }}>
        Hint: admin / admin (in dev)
      </div>
    </div>
  );
}