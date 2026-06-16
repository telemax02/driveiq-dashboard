// DriveIQ - invite-user Edge Function
// Lets an ADMIN invite a new user by email. Holds the service-role key (never in
// the browser), verifies the caller is an admin, then sends a Supabase invite.
//
// Deploy:   supabase functions deploy invite-user
// (SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY are injected automatically. If your
//  project only has the new key system, set the secret manually instead:
//    supabase secrets set SUPABASE_SECRET_KEY=sb_secret_xxx )

import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...CORS, "Content-Type": "application/json" },
  });
}

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: CORS });
  if (req.method !== "POST") return json({ error: "Method not allowed" }, 405);

  const url = Deno.env.get("SUPABASE_URL");
  const serviceKey =
    Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? Deno.env.get("SUPABASE_SECRET_KEY");
  if (!url || !serviceKey) {
    return json({ error: "Function not configured (missing service key)" }, 500);
  }

  // Service client - bypasses RLS, can perform auth admin actions.
  const admin = createClient(url, serviceKey, { auth: { persistSession: false } });

  // 1. Identify the caller from their access token.
  const jwt = (req.headers.get("Authorization") || "").replace(/^Bearer\s+/i, "");
  if (!jwt) return json({ error: "Not authenticated" }, 401);
  const { data: caller, error: callerErr } = await admin.auth.getUser(jwt);
  if (callerErr || !caller?.user) return json({ error: "Not authenticated" }, 401);

  // 2. The caller must be an admin.
  const { data: prof } = await admin
    .from("profiles")
    .select("role")
    .eq("id", caller.user.id)
    .single();
  if (!prof || prof.role !== "admin") return json({ error: "Admins only" }, 403);

  // 3. Validate the email and send the invite.
  let email = "";
  try {
    email = String(((await req.json()) as { email?: string }).email || "").trim();
  } catch (_) { /* no/invalid body */ }
  if (!email || !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) {
    return json({ error: "A valid email is required" }, 400);
  }

  const redirectTo = Deno.env.get("INVITE_REDIRECT_TO") || undefined;
  const { data, error } = await admin.auth.admin.inviteUserByEmail(
    email,
    redirectTo ? { redirectTo } : undefined,
  );
  if (error) return json({ error: error.message }, 400);

  return json({ ok: true, email: data?.user?.email ?? email });
});
