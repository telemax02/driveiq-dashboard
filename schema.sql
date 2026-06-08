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
alter table fleet_runs  enable row level security;
alter table latest_run  enable row level security;

-- Allow full access via anon key (internal tool — no auth required)
create policy "allow_all" on vehicles   for all to anon using (true) with check (true);
create policy "allow_all" on drivers    for all to anon using (true) with check (true);
create policy "allow_all" on trips      for all to anon using (true) with check (true);
create policy "allow_all" on incidents  for all to anon using (true) with check (true);
create policy "allow_all" on fleet_runs  for all to anon using (true) with check (true);
create policy "allow_all" on latest_run  for all to anon using (true) with check (true);
