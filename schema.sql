-- DriveIQ Supabase Schema
-- Run this in the Supabase SQL Editor: dashboard → SQL Editor → New query

create table if not exists vehicles (
  plate text primary key,
  make  text not null default ''
);

create table if not exists drivers (
  plate        text primary key,
  first_name   text default '',
  last_initial text default '',
  age          text default '',
  sex          text default '',
  notes        text default '',
  updated_at   timestamptz default now()
);

create table if not exists trips (
  id             bigint primary key,
  plate          text,
  run_ts         timestamptz default now(),
  date_str       text default '',
  time_str       text default '',
  km             integer default 0,
  raw            integer default 0,
  spd            integer,
  brk            integer default 0,
  acc            integer default 0,
  crn            integer default 0,
  cov_pct        integer default 0,
  incident       boolean default false,
  inc_mx         integer default 0,
  inc_dur        integer default 0,
  inc_avg        numeric default 0,
  from_addr      text default '',
  to_addr        text default '',
  slat           numeric,
  slon           numeric,
  elat           numeric,
  elon           numeric,
  begin_ts       bigint,
  end_ts         bigint,
  driving_period text default 'Day',
  rpm_s          integer default 0,
  lc             boolean default false
);

create table if not exists incidents (
  plate       text,
  trip_ref    text,
  primary key (plate, trip_ref),
  time_str    text default '',
  date_str    text default '',
  mx          integer default 0,
  dur         integer default 0,
  avg_speed   numeric default 0,
  datetime_str text default '',
  speed_str   text default '',
  loc         text default '',
  coords      jsonb default '[]',
  begin_ts    bigint,
  end_ts      bigint
);

-- Per-trip driven GPS path + harsh-event locations (lazy-loaded by the dashboard
-- when a trip's map is expanded). Kept OUT of latest_run to avoid bloating it.
create table if not exists trip_tracks (
  trip_id    bigint primary key,
  plate      text,
  track      jsonb default '[]',   -- [[lat,lon], ...] decimated driven path
  events     jsonb default '[]',   -- [{type,lat,lon,ts,sev}, ...] type in (brk,acc,crn,spd)
  updated_at timestamptz default now()
);

create table if not exists fleet_runs (
  id            serial primary key,
  run_ts        timestamptz default now(),
  fleet_avg     integer,
  num_vehicles  integer,
  total_trips   integer,
  num_incidents integer,
  date_range    text default ''
);

-- Live dashboard snapshot (single row, overwritten each pipeline run)
create table if not exists latest_run (
  id         int primary key default 1,
  data       jsonb,
  updated_at timestamptz default now()
);

-- Enable RLS on all tables
alter table vehicles    enable row level security;
alter table drivers     enable row level security;
alter table trips       enable row level security;
alter table incidents   enable row level security;
alter table trip_tracks enable row level security;
alter table fleet_runs  enable row level security;
alter table latest_run  enable row level security;

-- RLS POLICIES: do NOT open these tables to the anon/publishable key. The
-- invited-only access policies live in (run these AFTER this file, in order):
--   1. supabase_auth_setup.sql      (profiles, is_admin, profiles RLS)
--   2. supabase_rls_lockdown.sql    (allowed_emails, is_invited, read_invited on every data table)
--   3. supabase_user_logins.sql     (user_logins table + admin-read policy)
-- With RLS enabled above and NO policy added here, every table is locked by
-- default (deny-all) until the lockdown script adds the invited-only read policies.
--
-- WARNING: an "allow_all to anon ... using(true) with check(true)" policy used to
-- live here and was removed 17 Jun 2026. Do NOT re-add it: the publishable key is
-- shipped in the client, so that would grant the public full read+write to all
-- fleet data. supabase_rls_lockdown.sql is the single source of truth for policies.
