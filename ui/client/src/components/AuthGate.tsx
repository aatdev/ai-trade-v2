import { type ReactNode, useEffect } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { UNAUTHORIZED_EVENT, useAuthStatus } from '../api';
import { Loading } from './ui';
import Login from './Login';

/**
 * Wraps the app: blocks rendering behind a login screen when the server requires
 * a session and this client has none. When auth is disabled server-side, this is
 * transparent. Listens for the `auth:unauthorized` event so an expired session
 * mid-use bounces back to the login screen instead of silently failing.
 */
export default function AuthGate({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const { data, isLoading, isError } = useAuthStatus();

  useEffect(() => {
    const onUnauthorized = () => {
      void qc.invalidateQueries({ queryKey: ['auth'] });
    };
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
  }, [qc]);

  // First load: don't flash either the app or the login form.
  if (isLoading) return <Loading />;

  // If the status probe itself failed (network), let the app try — its own
  // queries will surface errors / trigger the 401 path if it's really auth.
  if (!isError && data && data.authRequired && !data.authenticated) {
    return <Login />;
  }

  return <>{children}</>;
}
