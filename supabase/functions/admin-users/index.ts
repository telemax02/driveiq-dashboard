// DriveIQ - admin-users Edge Function
// One admin-only endpoint for all user management. Holds the service-role key
// (never in the browser). Every action verifies the caller is an admin.
//
// POST body: { action: "list" | "invite" | "remove" | "resend" | "update", ... }
//
// Deploy:   supabase functions deploy admin-users
// (SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY are injected automatically. If your
//  project only has the new key system, set the secret manually instead:
//    supabase secrets set SUPABASE_SECRET_KEY=sb_secret_xxx )

import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};
const json = (body: unknown, status = 200): Response =>
  new Response(JSON.stringify(body), { status, headers: { ...CORS, "Content-Type": "application/json" } });

const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: CORS });
  if (req.method !== "POST") return json({ error: "Method not allowed" }, 405);

  const url = Deno.env.get("SUPABASE_URL");
  const serviceKey =
    Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? Deno.env.get("SUPABASE_SECRET_KEY");
  if (!url || !serviceKey) return json({ error: "Function not configured (missing service key)" }, 500);

  const admin = createClient(url, serviceKey, { auth: { persistSession: false } });

  // Caller must be an authenticated admin.
  const jwt = (req.headers.get("Authorization") || "").replace(/^Bearer\s+/i, "");
  if (!jwt) return json({ error: "Not authenticated" }, 401);
  const { data: caller, error: callerErr } = await admin.auth.getUser(jwt);
  if (callerErr || !caller?.user) return json({ error: "Not authenticated" }, 401);
  const { data: me } = await admin.from("profiles").select("role").eq("id", caller.user.id).single();
  if (!me || me.role !== "admin") return json({ error: "Admins only" }, 403);
  const callerId = caller.user.id;

  let body: Record<string, unknown> = {};
  try { body = (await req.json()) as Record<string, unknown>; } catch (_) { /* empty */ }
  const action = String(body.action || "");

  try {
    if (action === "list") {
      const { data, error } = await admin.auth.admin.listUsers({ page: 1, perPage: 1000 });
      if (error) return json({ error: error.message }, 400);
      const { data: profs } = await admin.from("profiles").select("id, role");
      const roleById: Record<string, string> = {};
      for (const p of profs ?? []) roleById[p.id as string] = p.role as string;
      const users = (data?.users ?? []).map((u) => ({
        id: u.id,
        email: u.email ?? "",
        name: (u.user_metadata?.name as string) ?? "",
        role: roleById[u.id] ?? "user",
        confirmed: !!(u.email_confirmed_at ?? u.confirmed_at),
        last_sign_in_at: u.last_sign_in_at ?? null,
      }));
      users.sort((a, b) => (a.email > b.email ? 1 : -1));
      return json({ users });
    }

    if (action === "invite" || action === "resend") {
      const email = String(body.email || "").trim();
      if (!EMAIL_RE.test(email)) return json({ error: "A valid email is required" }, 400);
      const redirectTo = Deno.env.get("INVITE_REDIRECT_TO") || undefined;
      const { data, error } = await admin.auth.admin.inviteUserByEmail(
        email,
        redirectTo ? { redirectTo } : undefined,
      );
      if (error) return json({ error: error.message }, 400);
      return json({ ok: true, email: data?.user?.email ?? email });
    }

    if (action === "remove") {
      const id = String(body.id || "");
      if (!id) return json({ error: "User id is required" }, 400);
      if (id === callerId) return json({ error: "You can't remove your own account" }, 400);
      const { error } = await admin.auth.admin.deleteUser(id);
      if (error) return json({ error: error.message }, 400);
      return json({ ok: true });
    }

    if (action === "update") {
      const id = String(body.id || "");
      if (!id) return json({ error: "User id is required" }, 400);
      const email = body.email != null ? String(body.email).trim() : undefined;
      const name = body.name != null ? String(body.name).trim() : undefined;
      const role = body.role != null ? String(body.role) : undefined;

      if (email !== undefined || name !== undefined) {
        if (email !== undefined && !EMAIL_RE.test(email)) return json({ error: "Invalid email" }, 400);
        const attrs: Record<string, unknown> = {};
        if (email !== undefined) attrs.email = email;
        if (name !== undefined) attrs.user_metadata = { name };
        const { error } = await admin.auth.admin.updateUserById(id, attrs);
        if (error) return json({ error: error.message }, 400);
        // keep profiles.email in sync
        if (email !== undefined) await admin.from("profiles").update({ email }).eq("id", id);
      }
      if (role !== undefined) {
        if (role !== "admin" && role !== "user") return json({ error: "Invalid role" }, 400);
        if (id === callerId && role !== "admin") {
          return json({ error: "You can't remove your own admin role" }, 400);
        }
        const { error } = await admin.from("profiles").update({ role }).eq("id", id);
        if (error) return json({ error: error.message }, 400);
      }
      return json({ ok: true });
    }

    return json({ error: "Unknown action" }, 400);
  } catch (e) {
    return json({ error: String((e as Error)?.message ?? e) }, 500);
  }
});
