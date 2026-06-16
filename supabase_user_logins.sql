-- ===========================================================================
-- DriveIQ - last seen IP per user (for the Admin > Users panel).
-- Reads Supabase's auth audit logs and returns the most recent IP + time per
-- user (any auth event that carries an IP -- login, token refresh, etc.), since
-- fresh "login" events are sparse and pruned. SECURITY DEFINER so it can read
-- the auth schema; EXECUTE is locked to the service role (the admin-users Edge
-- Function calls it). Run in Supabase -> SQL Editor. Safe to re-run.
-- ===========================================================================

create or replace function public.admin_user_logins()
returns table(user_id uuid, ip text, last_login timestamptz)
language sql
security definer
set search_path = public
as $$
  select distinct on ((e.payload->>'actor_id'))
         (e.payload->>'actor_id')::uuid                                  as user_id,
         coalesce(nullif(e.ip_address, ''), nullif(e.payload->>'ip_address','')) as ip,
         e.created_at                                                    as last_login
  from auth.audit_log_entries e
  where (e.payload->>'actor_id') is not null
    and coalesce(nullif(e.ip_address, ''), nullif(e.payload->>'ip_address','')) is not null
  order by (e.payload->>'actor_id'), e.created_at desc;
$$;

revoke execute on function public.admin_user_logins() from public, anon, authenticated;
grant  execute on function public.admin_user_logins() to service_role;
