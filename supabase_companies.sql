-- ===========================================================================
-- DriveIQ - companies (multi-tenant foundation)
-- Run this in Supabase -> SQL Editor, AFTER supabase_auth_setup.sql and
-- supabase_rls_lockdown.sql (this reuses public.is_invited() / public.is_admin()).
--
-- Adds a company/tenant layer: each company holds its own telematics fleet
-- (Flespi device IDs). The dashboard shows a company switcher; admins add/remove
-- companies in the Admin tab (in-browser, same pattern as driver edits).
-- Safe to re-run (idempotent).
-- ===========================================================================

create table if not exists public.companies (
  id                uuid primary key default gen_random_uuid(),
  slug              text unique not null,
  name              text not null,
  flespi_device_ids jsonb   default '[]',   -- ["6536476", "6605289", ...]
  flespi_calc_id    text    default '',
  color             text    default '',
  is_default        boolean default false,
  created_at        timestamptz not null default now()
);

-- RLS: invited users can read; only admins can write (mirrors drivers_* policies).
alter table public.companies enable row level security;
drop policy if exists companies_read        on public.companies;
drop policy if exists companies_admin_write on public.companies;
create policy companies_read        on public.companies for select to authenticated using (public.is_invited());
create policy companies_admin_write on public.companies for all    to authenticated
  using (public.is_admin()) with check (public.is_admin());

-- Seed the existing fleet as the default company.
-- Device IDs are the 13 devices hard-coded in score_v2.py (DEVS), calc id 2923614.
insert into public.companies (slug, name, flespi_device_ids, flespi_calc_id, is_default)
values (
  'telemax',
  'Telemax',
  '["6536476","6605289","6605473","6613713","6711239","6884310","6884322","6884325","7419562","7585062","7734421","7734429","8180103"]'::jsonb,
  '2923614',
  true
)
on conflict (slug) do nothing;
