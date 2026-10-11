-- ===========================================================================
-- DriveIQ - Stage 2: per-company access isolation (KARMO-isolated login)
-- Run in Supabase -> SQL Editor. Safe to re-run (idempotent).
--
-- MODEL
--   profiles.company_slug = the company a user is scoped to.
--     set   -> that user can ONLY read that company's rows.
--     NULL  -> defaults to 'telemax' (a new invite is useful immediately; an
--              admin then reassigns them from the Admin tab).
--   Admins always see everything.
--
-- FAIL-CLOSED ON DATA: rows whose `company` is NULL match nobody except admins,
-- so untagged data can never leak into the wrong tenant.
-- ===========================================================================

-- 1) Which company is a user scoped to? -------------------------------------
alter table public.profiles add column if not exists company_slug text;

create or replace function public.user_company()
returns text
language sql stable security definer set search_path = public
as $$
  select company_slug from public.profiles where id = auth.uid();
$$;

-- 2) Tag the per-tenant data tables with a company --------------------------
alter table public.trips       add column if not exists company text;
alter table public.incidents   add column if not exists company text;
alter table public.vehicles    add column if not exists company text;
alter table public.fleet_runs  add column if not exists company text;
alter table public.trip_tracks add column if not exists company text;
alter table public.drivers     add column if not exists company text;

update public.trips       set company = 'telemax' where company is null;
update public.incidents   set company = 'telemax' where company is null;
update public.vehicles    set company = 'telemax' where company is null;
update public.fleet_runs  set company = 'telemax' where company is null;
update public.trip_tracks set company = 'telemax' where company is null;
update public.drivers     set company = 'telemax' where company is null;

-- 3) Company-scoped read policies -------------------------------------------
-- Still invited-only. Admins see everything. A user with no company defaults
-- to Telemax rather than seeing every company.

drop policy if exists company_runs_read on public.company_runs;
create policy company_runs_read on public.company_runs for select to authenticated
  using ( public.is_invited() and ( public.is_admin()
          or company_slug = coalesce(public.user_company(), 'telemax') ) );

drop policy if exists companies_read on public.companies;
create policy companies_read on public.companies for select to authenticated
  using ( public.is_invited() and ( public.is_admin()
          or slug = coalesce(public.user_company(), 'telemax') ) );

-- latest_run is the legacy single-tenant (Telemax) snapshot: no company column.
drop policy if exists read_invited on public.latest_run;
create policy read_invited on public.latest_run for select to authenticated
  using ( public.is_invited() and ( public.is_admin()
          or coalesce(public.user_company(), 'telemax') = 'telemax' ) );

drop policy if exists read_invited on public.trips;
create policy read_invited on public.trips for select to authenticated
  using ( public.is_invited() and ( public.is_admin()
          or company = coalesce(public.user_company(), 'telemax') ) );

drop policy if exists read_invited on public.incidents;
create policy read_invited on public.incidents for select to authenticated
  using ( public.is_invited() and ( public.is_admin()
          or company = coalesce(public.user_company(), 'telemax') ) );

drop policy if exists read_invited on public.vehicles;
create policy read_invited on public.vehicles for select to authenticated
  using ( public.is_invited() and ( public.is_admin()
          or company = coalesce(public.user_company(), 'telemax') ) );

drop policy if exists read_invited on public.fleet_runs;
create policy read_invited on public.fleet_runs for select to authenticated
  using ( public.is_invited() and ( public.is_admin()
          or company = coalesce(public.user_company(), 'telemax') ) );

drop policy if exists read_invited on public.trip_tracks;
create policy read_invited on public.trip_tracks for select to authenticated
  using ( public.is_invited() and ( public.is_admin()
          or company = coalesce(public.user_company(), 'telemax') ) );

-- Drivers: scoped read; writes remain admin-only (drivers_admin_write).
drop policy if exists drivers_read on public.drivers;
create policy drivers_read on public.drivers for select to authenticated
  using ( public.is_invited() and ( public.is_admin()
          or company = coalesce(public.user_company(), 'telemax') ) );

-- 4) Assigning a user to a company ------------------------------------------
-- Admins hold profiles_admin_update, so the Admin tab's "Company access"
-- dropdown sets this directly. By SQL:
--   update public.profiles set company_slug = 'karmo-bne-qld' where email = '...';
