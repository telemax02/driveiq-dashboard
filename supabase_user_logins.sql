-- ===========================================================================
-- DriveIQ - per-user login IP + approx location (Admin > Users panel).
-- The auth audit log is empty on this project, so we record it ourselves:
-- the admin-users Edge Function's "record_login" action (called by the app
-- after sign-in) writes the caller's IP + city/country here. Admins read it.
-- Run in Supabase -> SQL Editor. Safe to re-run.
-- ===========================================================================

create table if not exists public.user_logins (
  user_id    uuid primary key references auth.users(id) on delete cascade,
  email      text,
  ip         text,
  city       text,
  country    text,
  last_login timestamptz
);

alter table public.user_logins enable row level security;

-- Admins can read everyone's login info. Writes happen only via the Edge
-- Function's service key (which bypasses RLS), so no write policy is needed.
drop policy if exists user_logins_admin_read on public.user_logins;
create policy user_logins_admin_read on public.user_logins
  for select to authenticated
  using (public.is_admin());

-- Clean up the earlier (unused) audit-log approach.
drop function if exists public.admin_user_logins();
drop function if exists public.admin_audit_debug();
