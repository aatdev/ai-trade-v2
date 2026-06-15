import { type FormEvent, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { login } from '../api';

/** Login screen shown by AuthGate when a session is required but absent. */
export default function Login() {
  const qc = useQueryClient();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = await login(username, password);
      if (res.ok) {
        // Re-fetch auth status (and let other queries refetch) → reveals the app.
        await qc.invalidateQueries();
      } else {
        setError(res.error || 'Не удалось войти');
      }
    } catch {
      setError('Ошибка соединения');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-screen">
      <form className="login-card" onSubmit={onSubmit}>
        <h1>📊 Trading State</h1>
        <p className="muted">Войдите, чтобы продолжить</p>
        <label>
          Логин
          <input
            type="text"
            autoComplete="username"
            autoFocus
            value={username}
            onChange={(e) => setUsername(e.target.value)}
          />
        </label>
        <label>
          Пароль
          <input
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        {error ? <div className="err">{error}</div> : null}
        <button className="primary" type="submit" disabled={busy || !username || !password}>
          {busy ? 'Вход…' : 'Войти'}
        </button>
      </form>
    </div>
  );
}
