-- ============================================================================
-- DriveIQ — authentication, roles & row-level security setup
-- Run this in Supabase → SQL Editor.
--
-- PART A is ADDITIVE and safe to run now. It does NOT change who can read the
-- existing data — it only adds the profiles table, the admin role, and helpers.
--
-- PART B turns on Row-Level Security (makes the data require a signed-in user).
-- DO NOT run Part B until the login gate is deployed on the dashboard, or the
-- public page will lose read access and go blank. We'll run it in Phase 3.
-- ============================================================================


-- ========================= PART A — run now (safe) ==========================

-- One profile row per auth user, carrying the role.
create table if not exists public.profiles (
  id         uuid primary key references auth.users(id) on delete cascade,
  email      text,
  role       text not null default 'user' check (role in ('admin','user')),
  created_at timestamptz not null default now()
);

-- Auto-create a 'user' profile whenever a new auth user is added (signup/invite).
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.profiles (id, email, role)
  values (new.id, new.email, 'user')
  on conflict (id) do nothing;
  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

-- Helper used by RLS policies and the app to check the current user's role.
create or replace function public.is_admin()
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (
    select 1 from public.profiles
    where id = auth.uid() and role = 'admin'
  );
$$;

-- Backfill profiles for any users that already exist.
insert into public.profiles (id, email, role)
select id, email, 'user' from auth.users
on conflict (id) do nothing;

-- Lock down the profiles table itself (safe — only governs this new table).
alter table public.profiles enable row level security;

drop policy if exists profiles_read on public.profiles;
create policy profiles_read on public.profiles
  for select to authenticated
  using (id = auth.uid() or public.is_admin());

drop policy if exists profiles_admin_update on public.profiles;
create policy profiles_admin_update on public.profiles
  for update to authenticated
  using (public.is_admin()) with check (public.is_admin());

-- >>> AFTER you have created your own account (Supabase → Auth → Users → Add user,
--     email + password), make yourself the admin by running:
--
--     update public.profiles set role = 'admin'
--     where email = 'ash.phayer@telemax.com.au';


-- ============== PART B — RUN ONLY AFTER THE LOGIN GATE IS LIVE ===============
-- Prerequisites before running:
--   1. The dashboard login screen is deployed (Phase 2).
--   2. The CI pipeline is writing with the SECRET key (SUPABASE_SECRET_KEY),
--      otherwise the 4-hourly updates will fail once RLS is on.
--
-- Effect: reads require a signed-in user; the secret key (used by the pipeline)
-- bypasses RLS so the data writers keep working; driver edits are admin-only.
-- Uncomment the block below and run it in Phase 3.
/*
alter table public.latest_run  enable row level security;
alter table public.trips       enable row level security;
alter table public.trip_tracks enable row level security;
alter table public.incidents   enable row level security;
alter table public.vehicles    enable row level security;
alter table public.fleet_runs  enable row level security;
alter table public.drivers     enable row level security;

-- Read = any signed-in user. (No write policies => only the secret key can write.)
create policy read_authenticated on public.latest_run  for select to authenticated using (true);
create policy read_authenticated on public.trips       for select to authenticated using (true);
create policy read_authenticated on public.trip_tracks for select to authenticated using (true);
create policy read_authenticated on public.incidents   for select to authenticated using (true);
create policy read_authenticated on public.vehicles    for select to authenticated using (true);
create policy read_authenticated on public.fleet_runs  for select to authenticated using (true);

-- Drivers: any signed-in user can read; only admins can edit (edits happen in-browser).
create policy drivers_read        on public.drivers for select to authenticated using (true);
create policy drivers_admin_write on public.drivers for all    to authenticated
  using (public.is_admin()) with check (public.is_admin());
*/
