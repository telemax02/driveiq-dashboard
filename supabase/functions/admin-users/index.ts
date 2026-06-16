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
  const callerId = caller.user.id;

  let body: Record<string, unknown> = {};
  try { body = (await req.json()) as Record<string, unknown>; } catch (_) { /* empty */ }
  const action = String(body.action || "");

  try {
    // Any signed-in user may record their OWN session IP + approx location.
    if (action === "record_login") {
      const xff = req.headers.get("x-forwarded-for") || "";
      const ip = (xff.split(",")[0] || req.headers.get("x-real-ip") || "").trim();
      const now = new Date().toISOString();
      const email = caller.user.email ?? null;
      if (!ip) {
        await admin.from("user_logins").upsert({ user_id: callerId, email, last_login: now }, { onConflict: "user_id" });
        return json({ ok: true });
      }
      // Only geo-locate when the IP changed (saves geo-IP lookups).
      const { data: existing } = await admin.from("user_logins").select("ip, city, country").eq("user_id", callerId).maybeSingle();
      let city = (existing?.city as string) || "", country = (existing?.country as string) || "";
      if (!existing || existing.ip !== ip || (!city && !country)) {
        try {
          const r = await fetch("https://ipapi.co/" + encodeURIComponent(ip) + "/json/", { headers: { "User-Agent": "DriveIQ" } });
          const j = await r.json(); city = j.city || ""; country = j.country_name || j.country || "";
        } catch (_) { /* best-effort geo */ }
      }
      await admin.from("user_logins").upsert({ user_id: callerId, email, ip, city, country, last_login: now }, { onConflict: "user_id" });
      return json({ ok: true });
    }

    // Everything below is admin-only.
    const { data: me } = await admin.from("profiles").select("role").eq("id", callerId).single();
    if (!me || me.role !== "admin") return json({ error: "Admins only" }, 403);

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
      // Attach last recorded login IP + approx location (from user_logins, which
      // the record_login action writes; harmless if the table isn't there yet).
      try {
        const { data: ul } = await admin.from("user_logins").select("user_id, ip, city, country");
        const by: Record<string, { ip?: string; city?: string; country?: string }> = {};
        for (const r of (ul ?? []) as Array<{ user_id: string; ip: string; city: string; country: string }>) by[r.user_id] = r;
        for (const u of users) {
          const rec = by[u.id];
          (u as Record<string, unknown>).ip = rec?.ip || "";
          (u as Record<string, unknown>).loc = rec ? [rec.city, rec.country].filter(Boolean).join(", ") : "";
        }
      } catch (_) { /* user_logins not created yet */ }
      return json({ users });
    }

    if (action === "invite" || action === "resend") {
      const email = String(body.email || "").trim();
      if (!EMAIL_RE.test(email)) return json({ error: "A valid email is required" }, 400);
      const name = body.name != null ? String(body.name).trim() : "";
      const role = body.role === "admin" ? "admin" : "user";
      // Authorize this email (allowlist) regardless of whether the email delivers.
      await admin.from("allowed_emails").upsert({ email: email.toLowerCase() }, { onConflict: "email" });
      const opts: Record<string, unknown> = {};
      const redirectTo = Deno.env.get("INVITE_REDIRECT_TO") || undefined;
      if (redirectTo) opts.redirectTo = redirectTo;
      if (name) opts.data = { name }; // -> user_metadata.name (the display name)
      const { data, error } = await admin.auth.admin.inviteUserByEmail(
        email,
        Object.keys(opts).length ? opts : undefined,
      );
      if (error) {
        // On "resend", a user who already accepted shows up as already-registered.
        // The allowlist upsert above already (re)authorised them, so report success.
        if (action === "resend" && /already|exists|registered/i.test(error.message)) {
          return json({ ok: true, email });
        }
        return json({ error: error.message }, 400);
      }
      // Apply the chosen role on a fresh invite (the handle_new_user trigger
      // creates the profile as 'user'; this upsert overrides it). Resend never
      // changes the role.
      const newId = data?.user?.id;
      if (action === "invite" && newId) {
        await admin.from("profiles").upsert({ id: newId, email, role }, { onConflict: "id" });
      }
      return json({ ok: true, email: data?.user?.email ?? email });
    }

    if (action === "remove") {
      const id = String(body.id || "");
      if (!id) return json({ error: "User id is required" }, 400);
      if (id === callerId) return json({ error: "You can't remove your own account" }, 400);
      // Resolve the email BEFORE deleting so we can clear the allowlist grant.
      // Fall back to profiles.email if the auth lookup is empty (transient/null email).
      const { data: target } = await admin.auth.admin.getUserById(id);
      let targetEmail = target?.user?.email?.toLowerCase();
      if (!targetEmail) {
        const { data: prof } = await admin.from("profiles").select("email").eq("id", id).maybeSingle();
        const pe = prof?.email as string | undefined;
        if (pe) targetEmail = pe.toLowerCase();
      }
      const { error } = await admin.auth.admin.deleteUser(id);
      if (error) return json({ error: error.message }, 400);
      if (targetEmail) {
        await admin.from("allowed_emails").delete().eq("email", targetEmail);
        return json({ ok: true });
      }
      return json({
        ok: true,
        warning: "User deleted, but their email could not be resolved to clear the invite allowlist. Check allowed_emails manually.",
      });
    }

    if (action === "update") {
      const id = String(body.id || "");
      if (!id) return json({ error: "User id is required" }, 400);
      const email = body.email != null ? String(body.email).trim() : undefined;
      const name = body.name != null ? String(body.name).trim() : undefined;
      const role = body.role != null ? String(body.role) : undefined;

      if (email !== undefined || name !== undefined) {
        if (email !== undefined && !EMAIL_RE.test(email)) return json({ error: "Invalid email" }, 400);

        // Only when an email is supplied do we need the current account: to tell a
        // real rename from the unchanged email the UI always re-sends, and to know
        // the old allowlist key. Bail safely if we can't load it (don't half-apply).
        let oldEmail: string | undefined;
        if (email !== undefined) {
          const { data: current, error: curErr } = await admin.auth.admin.getUserById(id);
          if (curErr || !current?.user) return json({ error: "Could not load that user; please try again." }, 404);
          oldEmail = current.user.email?.toLowerCase();
        }

        const emailChanged = email !== undefined && email.toLowerCase() !== oldEmail;
        const attrs: Record<string, unknown> = {};
        if (emailChanged) attrs.email = email;
        if (name !== undefined) attrs.user_metadata = { name };
        if (Object.keys(attrs).length) {
          const { error } = await admin.auth.admin.updateUserById(id, attrs);
          if (error) return json({ error: error.message }, 400);
        }
        if (emailChanged) {
          const newEmail = (email as string).toLowerCase();
          // keep profiles.email in sync (lowercased, like the allowlist + backfill)
          await admin.from("profiles").update({ email: newEmail }).eq("id", id);
          // Keep the invite grant attached to the new email so a renamed user
          // isn't locked out by RLS. If the OLD email was known and NOT on the
          // allowlist, leave them off (never re-authorise a removed grant);
          // otherwise authorise the new email.
          if (oldEmail) {
            const { data: had } = await admin.from("allowed_emails")
              .select("email").eq("email", oldEmail).maybeSingle();
            if (had) {
              await admin.from("allowed_emails").delete().eq("email", oldEmail);
              await admin.from("allowed_emails").upsert({ email: newEmail }, { onConflict: "email" });
            }
          } else {
            await admin.from("allowed_emails").upsert({ email: newEmail }, { onConflict: "email" });
          }
        }
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

    if (action === "geoip") {
      const ip = String(body.ip || "").trim();
      if (!ip) return json({ city: "", region: "", country: "" });
      try {
        const r = await fetch("https://ipapi.co/" + encodeURIComponent(ip) + "/json/", {
          headers: { "User-Agent": "DriveIQ-admin" },
        });
        const j = await r.json();
        return json({ city: j.city || "", region: j.region || "", country: j.country_name || j.country || "" });
      } catch (_) {
        return json({ city: "", region: "", country: "" });
      }
    }

    return json({ error: "Unknown action" }, 400);
  } catch (e) {
    return json({ error: String((e as Error)?.message ?? e) }, 500);
  }
});
