/**
 * Custom username/password auth helpers.
 *
 * Session is stored in:
 *   - localStorage  → for client-side reads (Topbar, etc.)
 *   - cookie        → for server-side proxy route protection
 *
 * The Supabase client is used only as a DB client (rpc calls),
 * NOT for Supabase Auth.
 */

import { createClient } from "@/lib/supabase/client";

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
  const supabase = createClient();

  const { data, error } = await supabase.rpc("register_user", {
    p_username: username.trim().toLowerCase(),
    p_password: password,
    p_full_name: fullName?.trim() || username.trim(),
  });

  if (error) return { error: error.message };

  const result = data as { error?: string; id?: string; username?: string };
  if (result.error) return { error: result.error };

  const user: AuthUser = {
    id: result.id!,
    username: result.username!,
    full_name: fullName?.trim() || username.trim(),
    role: "investigator",
  };

  setSession(user);
  return { user };
}

export async function login(
  username: string,
  password: string
): Promise<{ user: AuthUser } | { error: string }> {
  const supabase = createClient();

  const { data, error } = await supabase.rpc("login_user", {
    p_username: username.trim().toLowerCase(),
    p_password: password,
  });

  if (error) return { error: error.message };

  const result = data as {
    error?: string;
    id?: string;
    username?: string;
    full_name?: string;
    role?: string;
  };
  if (result.error) return { error: result.error };

  const user: AuthUser = {
    id: result.id!,
    username: result.username!,
    full_name: result.full_name || result.username!,
    role: result.role || "investigator",
  };

  setSession(user);
  return { user };
}

export function logout() {
  clearSession();
}
