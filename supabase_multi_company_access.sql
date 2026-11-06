-- ===========================================================================
-- DriveIQ - Stage 3: multi-company access (a user can be granted MORE THAN ONE
-- company, e.g. KARMO staff who should also see Telemax).
-- Run in Supabase -> SQL Editor. Safe to re-run (idempotent).
--
-- WHAT CHANGES
--   Stage 2 gave each profile ONE company (profiles.company_slug). This adds
--   profiles.company_slugs text[] - the SET of companies a user may read - and
--   backfills it from company_slug so nobody's access changes when this runs.
--
-- MODEL
--   company_slugs = ['telemax','karmo-bne-qld']  -> user sees both
--   company_slugs = ['karmo-bne-qld']            -> KARMO only
--   company_slugs = NULL or '{}'                 -> defaults to ['telemax']
--   role = 'admin'                               -> sees everything, always
--
-- Granting "all companies" means ticking every box, which stores the explicit
-- list. There is deliberately no wildcard: a tenant added later must be granted
-- on purpose, never inherited by an existing user.
--
-- FAIL-CLOSED ON DATA: can_see_company(NULL) is NULL -> false, so a row whose
-- `company` was never tagged stays invisible to non-admins.
-- ===========================================================================

-- 1) The set of companies a user may read -----------------------------------
alter table public.profiles add column if not exists company_slugs text[];

-- Backfill from Stage 2's single-company column. Only fills rows that have no
-- set yet, so re-running can never clobber a multi-company grant.
update public.profiles
   set company_slugs = array[company_slug]
 where company_slug is not null
   and (company_slugs is null or company_slugs = '{}');

create or replace function public.user_companies()
returns text[]
language sql stable security definer set search_path = public
as $$
  -- Prefer the set; fall back to Stage 2's single column so access still works
  -- in the window before the frontend that writes company_slugs is deployed.
  select coalesce(
           nullif(company_slugs, '{}'),
           case when company_slug is not null then array[company_slug] end
         )
    from public.profiles
   where id = auth.uid();
$$;

-- One predicate for every table, so the nine policies below can't drift apart.
create or replace function public.can_see_company(c text)
returns boolean
language sql stable security definer set search_path = public
as $$
  select public.is_admin()
      or c = any(coalesce(public.user_companies(), array['telemax']));
$$;

-- 2) Re-point every read policy at the new predicate -------------------------
-- Still invited-only; admins still see everything.

drop policy if exists company_runs_read on public.company_runs;
create policy company_runs_read on public.company_runs for select to authenticated
  using ( public.is_invited() and public.can_see_company(company_slug) );

drop policy if exists companies_read on public.companies;
create policy companies_read on public.companies for select to authenticated
  using ( public.is_invited() and public.can_see_company(slug) );

-- latest_run is the legacy single-tenant (Telemax) snapshot: no company column.
drop policy if exists read_invited on public.latest_run;
create policy read_invited on public.latest_run for select to authenticated
  using ( public.is_invited() and public.can_see_company('telemax') );

drop policy if exists read_invited on public.trips;
create policy read_invited on public.trips for select to authenticated
  using ( public.is_invited() and public.can_see_company(company) );

drop policy if exists read_invited on public.incidents;
create policy read_invited on public.incidents for select to authenticated
  using ( public.is_invited() and public.can_see_company(company) );

drop policy if exists read_invited on public.vehicles;
create policy read_invited on public.vehicles for select to authenticated
  using ( public.is_invited() and public.can_see_company(company) );

drop policy if exists read_invited on public.fleet_runs;
create policy read_invited on public.fleet_runs for select to authenticated
  using ( public.is_invited() and public.can_see_company(company) );

drop policy if exists read_invited on public.trip_tracks;
create policy read_invited on public.trip_tracks for select to authenticated
  using ( public.is_invited() and public.can_see_company(company) );

-- Drivers: scoped read; writes remain admin-only (drivers_admin_write).
drop policy if exists drivers_read on public.drivers;
create policy drivers_read on public.drivers for select to authenticated
  using ( public.is_invited() and public.can_see_company(company) );

-- 3) Granting access ---------------------------------------------------------
-- The Admin tab's "Company access" cell writes this array directly (admins hold
-- profiles_admin_update). By SQL, to let a KARMO user also see Telemax:
--   update public.profiles
--      set company_slugs = array['telemax','karmo-bne-qld']
--    where email = 'someone@karmo.co';
--
-- Check who can see what:
--   select email, role, company_slug, company_slugs from public.profiles
--    order by email;
