// src/pages/Login.jsx
import React, { useState } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import { setAuthToken, loginRequest } from "../api"; // <-- percorso corretto

export default function Login({ onLogged }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError]       = useState(null);
  const [loading, setLoading]   = useState(false);

  const navigate = useNavigate();
  const location = useLocation();

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);
    setError(null);

    try {
      const res = await loginRequest(username, password);

      // salva e attiva il token per le chiamate axios
      sessionStorage.setItem("eg_token", res.token);
      setAuthToken(res.token);

      // NOTIFICA il parent con il token (così aggiorna lo stato logged)
      onLogged?.(res.token);

      // naviga alla destinazione protetta (microtask -> evita tempi di propagazione stato)
      const dest = location.state?.from?.pathname || "/admin";
      queueMicrotask(() => navigate(dest, { replace: true }));
    } catch (e) {
      const msg = e?.response?.data?.detail || e.message || "Login fallito";
      setError(msg);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="login-page">
      <h2>Login Admin</h2>
      <form onSubmit={handleSubmit}>
        <div>
          <label>Username</label>
          <input
            type="text"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            disabled={loading}
          />
        </div>
        <div>
          <label>Password</label>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            disabled={loading}
          />
        </div>
        {error && <p style={{ color: "red" }}>{error}</p>}
        <button type="submit" disabled={loading}>
          {loading ? "Attendere..." : "Login"}
        </button>
      </form>
    </div>
  );
}
