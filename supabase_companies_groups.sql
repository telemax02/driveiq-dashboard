-- ===========================================================================
-- DriveIQ - companies: group + device-model support (follow-on migration)
-- Run in Supabase -> SQL Editor, AFTER supabase_companies.sql. Additive & idempotent.
--
-- Lets a company be defined by a Flespi GROUP + a device MODEL filter (so the
-- fleet auto-stays-in-sync) instead of a fixed list of device ids.
-- Also seeds KARMO (BNE/QLD) = Flespi group 586084, model fmc003.
-- ===========================================================================

alter table public.companies add column if not exists flespi_group_id text default '';
alter table public.companies add column if not exists device_type     text default '';

-- Seed KARMO: group 586084 holds 869 devices; filtering to model fmc003 selects
-- 440 (the other 429 are model tld2_d and are excluded).
insert into public.companies (slug, name, flespi_group_id, device_type, is_default)
values ('karmo-bne-qld', 'KARMO (BNE/QLD)', '586084', 'fmc003', false)
on conflict (slug) do nothing;
