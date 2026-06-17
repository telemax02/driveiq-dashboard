-- One-time migration: trip_tracks primary key  trip_id  ->  (plate, trip_id)
--
-- WHY: the trip id is the Flespi calc interval id, which is unique PER DEVICE but
-- COLLIDES ACROSS DEVICES (e.g. interval id 3790 exists for both 344IT2 and 934NQ4).
-- With trip_id as the sole primary key, two vehicles' same-id trips collapse into
-- one row and one overwrites the other, so the dashboard showed the wrong vehicle's
-- GPS track + harsh-event markers for the colliding trip.
--
-- Run this ONCE in the Supabase SQL Editor (DDL cannot go through the REST key).
-- Safe + idempotent: existing rows keep their (plate, trip_id); no data is deleted.

alter table trip_tracks alter column trip_id set not null;
update  trip_tracks set plate = '' where plate is null;
alter table trip_tracks alter column plate set not null;
alter table trip_tracks drop  constraint if exists trip_tracks_pkey;
alter table trip_tracks add   constraint trip_tracks_pkey primary key (plate, trip_id);
