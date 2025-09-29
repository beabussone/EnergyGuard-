// src/auth.jsx
import React, { createContext, useContext, useEffect, useState } from 'react';
import { setAuthToken, loginRequest } from './api';

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [token, setToken] = useState(() => localStorage.getItem('eg_token') || null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    setAuthToken(token);
  }, [token]);

  const login = async (username, password) => {
    setLoading(true);
    try {
      const { token } = await loginRequest(username, password);
      setToken(token);
      localStorage.setItem('eg_token', token);
      return { ok: true };
    } catch (e) {
      return { ok: false, error: e?.response?.data?.detail || 'Credenziali non valide' };
    } finally {
      setLoading(false);
    }
  };

  const logout = () => {
    setToken(null);
    localStorage.removeItem('eg_token');
    setAuthToken(null);
  };

  return (
    <AuthContext.Provider value={{ token, loading, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  return useContext(AuthContext);
}