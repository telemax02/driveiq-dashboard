-- ===========================================================================
-- DriveIQ - Stage 2: per-company access isolation (KARMO-isolated login)
-- Run in Supabase -> SQL Editor, AFTER supabase_companies.sql,
-- supabase_companies_groups.sql and supabase_companies_scoring.sql.
-- Safe to re-run (idempotent).
--
-- MODEL
--   profiles.company_slug = the company a user is scoped to.
--     NULL  -> unscoped: sees everything, exactly as today (backwards compatible,
--              so existing users and the live production dashboard keep working).
--     set   -> that user can ONLY read that company's rows.
--   Admins always see everything.
--
-- FAIL-CLOSED: rows whose `company` is NULL are visible only to admins and
-- unscoped users — never to a scoped user — so any future untagged row cannot
-- leak into the wrong tenant.
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

-- Everything that exists today belongs to Telemax.
update public.trips       set company = 'telemax' where company is null;
update public.incidents   set company = 'telemax' where company is null;
update public.vehicles    set company = 'telemax' where company is null;
update public.fleet_runs  set company = 'telemax' where company is null;
update public.trip_tracks set company = 'telemax' where company is null;
update public.drivers     set company = 'telemax' where company is null;

-- 3) Company-scoped read policies -------------------------------------------
-- Still invited-only; admins and unscoped users unchanged; scoped users see
-- only their own company.

drop policy if exists company_runs_read on public.company_runs;
create policy company_runs_read on public.company_runs for select to authenticated
  using ( public.is_invited() and ( public.is_admin()
          or public.user_company() is null
          or company_slug = public.user_company() ) );

drop policy if exists companies_read on public.companies;
create policy companies_read on public.companies for select to authenticated
  using ( public.is_invited() and ( public.is_admin()
          or public.user_company() is null
          or slug = public.user_company() ) );

-- latest_run is the legacy single-tenant (Telemax) snapshot: no company column.
drop policy if exists read_invited on public.latest_run;
create policy read_invited on public.latest_run for select to authenticated
  using ( public.is_invited() and ( public.is_admin()
          or public.user_company() is null
          or public.user_company() = 'telemax' ) );

drop policy if exists read_invited on public.trips;
create policy read_invited on public.trips for select to authenticated
  using ( public.is_invited() and ( public.is_admin()
          or public.user_company() is null
          or company = public.user_company() ) );

drop policy if exists read_invited on public.incidents;
create policy read_invited on public.incidents for select to authenticated
  using ( public.is_invited() and ( public.is_admin()
          or public.user_company() is null
          or company = public.user_company() ) );

drop policy if exists read_invited on public.vehicles;
create policy read_invited on public.vehicles for select to authenticated
  using ( public.is_invited() and ( public.is_admin()
          or public.user_company() is null
          or company = public.user_company() ) );

drop policy if exists read_invited on public.fleet_runs;
create policy read_invited on public.fleet_runs for select to authenticated
  using ( public.is_invited() and ( public.is_admin()
          or public.user_company() is null
          or company = public.user_company() ) );

drop policy if exists read_invited on public.trip_tracks;
create policy read_invited on public.trip_tracks for select to authenticated
  using ( public.is_invited() and ( public.is_admin()
          or public.user_company() is null
          or company = public.user_company() ) );

-- Drivers: scoped read; writes remain admin-only (drivers_admin_write).
drop policy if exists drivers_read on public.drivers;
create policy drivers_read on public.drivers for select to authenticated
  using ( public.is_invited() and ( public.is_admin()
          or public.user_company() is null
          or company = public.user_company() ) );

-- 4) Assigning a user to a company ------------------------------------------
-- Admins already hold profiles_admin_update (supabase_auth_setup.sql), so the
-- Admin tab can set this directly:
--   update public.profiles set company_slug = 'karmo-bne-qld' where email = '...';
-- Clear it (back to full access) with company_slug = null.
