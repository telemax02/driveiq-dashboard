-- ===========================================================================
-- DriveIQ - per-company scoring snapshots (Stage 1, additive & production-safe)
-- Run in Supabase -> SQL Editor, AFTER supabase_companies.sql + _groups.sql.
--
-- Adds a NEW table company_runs (one live dashboard snapshot per company),
-- leaving the existing single-tenant latest_run table completely untouched so
-- the live Telemax pipeline/dashboard on `main` keep working unchanged.
-- Also records KARMO's scoring calc on its company row.
-- Safe to re-run (idempotent).
-- ===========================================================================

create table if not exists public.company_runs (
  company_slug text primary key,
  data         jsonb,
  updated_at   timestamptz not null default now()
);

-- RLS: invited users can read (same as latest_run today). Stage 2 tightens this
-- to per-company (is_admin() OR company_slug = user_company()).
alter table public.company_runs enable row level security;
drop policy if exists company_runs_read on public.company_runs;
create policy company_runs_read on public.company_runs
  for select to authenticated using (public.is_invited());

-- KARMO's dedicated scoring calc (created via the Flespi API during the pilot).
update public.companies set flespi_calc_id = '3510155' where slug = 'karmo-bne-qld';
