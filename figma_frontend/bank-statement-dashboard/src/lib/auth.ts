/**
 * Custom username/password auth helpers.
 *
 * Session is stored in:
 *   - localStorage  → for client-side reads (Topbar, etc.)
 *   - cookie        → for server-side proxy route protection
 */

const BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

export type AuthUser = {
  id: string;
  username: string;
  full_name: string;
  role: string;
};

const SESSION_KEY = "cidecode_user";
const SESSION_COOKIE = "cidecode_session";

// ─── Session helpers ─────────────────────────────────────────────────────────

export function getSession(): AuthUser | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = localStorage.getItem(SESSION_KEY);
    return raw ? (JSON.parse(raw) as AuthUser) : null;
  } catch {
    return null;
  }
}

function setSession(user: AuthUser) {
  localStorage.setItem(SESSION_KEY, JSON.stringify(user));
  // Also set a cookie so the proxy can read it server-side
  document.cookie = `${SESSION_COOKIE}=${encodeURIComponent(
    JSON.stringify(user)
  )}; path=/; max-age=${60 * 60 * 24 * 7}; SameSite=Lax`;
}

export function clearSession() {
  localStorage.removeItem(SESSION_KEY);
  // Expire the cookie
  document.cookie = `${SESSION_COOKIE}=; path=/; max-age=0; SameSite=Lax`;
}

// ─── Auth actions ─────────────────────────────────────────────────────────────

export async function register(
  username: string,
  password: string,
  fullName?: string
): Promise<{ user: AuthUser } | { error: string }> {
  try {
    const res = await fetch(`${BASE}/api/v1/auth/register`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username: username.trim().toLowerCase(),
        password: password,
        full_name: fullName?.trim() || username.trim(),
      }),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: "Registration failed" }));
      return { error: err.detail || "Registration failed" };
    }

    const result = await res.json();
    const user: AuthUser = {
      id: result.id,
      username: result.username,
      full_name: result.full_name,
      role: result.role || "investigator",
    };

    setSession(user);
    return { user };
  } catch (err) {
    return { error: err instanceof Error ? err.message : "Failed to register" };
  }
}

export async function login(
  username: string,
  password: string
): Promise<{ user: AuthUser } | { error: string }> {
  try {
    const res = await fetch(`${BASE}/api/v1/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username: username.trim().toLowerCase(),
        password: password,
      }),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: "Incorrect username or password" }));
      return { error: err.detail || "Incorrect username or password" };
    }

    const result = await res.json();
    const user: AuthUser = {
      id: result.id,
      username: result.username,
      full_name: result.full_name,
      role: result.role || "investigator",
    };

    setSession(user);
    return { user };
  } catch (err) {
    return { error: err instanceof Error ? err.message : "Failed to login" };
  }
}

export function logout() {
  clearSession();
}
