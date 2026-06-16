-- ===========================================================================
-- DriveIQ - data lockdown (RLS) + invited-only allowlist
-- Run this in Supabase -> SQL Editor.
--
-- PREREQUISITES (do these first, in order):
--   1. SUPABASE_SECRET_KEY (your sb_secret_... key) is set as a GitHub Actions
--      secret, so the 4-hourly pipeline keeps writing once RLS is on.
--   2. The admin-users Edge Function has been re-deployed (allowlist support).
--
-- Effect: the fleet data becomes readable ONLY by a signed-in, invited user.
-- The pipeline writes with the secret key (bypasses RLS). Driver edits = admins.
-- Safe to re-run (idempotent).
-- ===========================================================================

-- 1) Allowlist of invited emails -------------------------------------------------
create table if not exists public.allowed_emails (
  email      text primary key,
  created_at timestamptz not null default now()
);

-- Backfill: everyone who already has an account stays authorised.
insert into public.allowed_emails (email)
  select distinct lower(email) from public.profiles where email is not null
on conflict (email) do nothing;
insert into public.allowed_emails (email)
  select distinct lower(email) from auth.users where email is not null
on conflict (email) do nothing;

alter table public.allowed_emails enable row level security;
drop policy if exists allowed_self_read on public.allowed_emails;
create policy allowed_self_read on public.allowed_emails
  for select to authenticated
  using (lower(email) = lower(auth.jwt() ->> 'email'));

-- 2) "Is this signed-in user invited?" (admins always pass) ----------------------
create or replace function public.is_invited()
returns boolean
language sql stable security definer set search_path = public
as $$
  select public.is_admin()
      or exists (
        select 1 from public.allowed_emails
        where lower(email) = lower(auth.jwt() ->> 'email')
      );
$$;

-- 3) Clear any leftover policies on the data tables (from earlier experiments) ---
do $$
declare r record;
begin
  for r in
    select policyname, tablename from pg_policies
    where schemaname = 'public'
      and tablename in ('latest_run','trips','trip_tracks','incidents','vehicles','fleet_runs','drivers')
  loop
    execute format('drop policy if exists %I on public.%I', r.policyname, r.tablename);
  end loop;
end $$;

-- 4) Turn RLS on and allow reads only for invited, signed-in users ---------------
alter table public.latest_run  enable row level security;
alter table public.trips       enable row level security;
alter table public.trip_tracks enable row level security;
alter table public.incidents   enable row level security;
alter table public.vehicles    enable row level security;
alter table public.fleet_runs  enable row level security;
alter table public.drivers     enable row level security;

create policy read_invited on public.latest_run  for select to authenticated using (public.is_invited());
create policy read_invited on public.trips       for select to authenticated using (public.is_invited());
create policy read_invited on public.trip_tracks for select to authenticated using (public.is_invited());
create policy read_invited on public.incidents   for select to authenticated using (public.is_invited());
create policy read_invited on public.vehicles    for select to authenticated using (public.is_invited());
create policy read_invited on public.fleet_runs  for select to authenticated using (public.is_invited());

-- Drivers: invited users can read; only admins can edit (edits happen in-browser).
create policy drivers_read        on public.drivers for select to authenticated using (public.is_invited());
create policy drivers_admin_write on public.drivers for all    to authenticated
  using (public.is_admin()) with check (public.is_admin());

-- (No INSERT/UPDATE/DELETE policies on the other tables => only the secret key,
--  which bypasses RLS, can write them. That's the CI pipeline.)
