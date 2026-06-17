"""
Syncs processed scores_only.json to Supabase after each pipeline run.
Tables: vehicles, trips, incidents, fleet_runs, latest_run (live dashboard)
"""
import json, os, datetime, urllib.request, urllib.error
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))
# fleet-insight display fields are recomputed live each run (only the AI prose is weekly)
from fleet_insight import _fleet_components, _trip_mix, _risk, RISK_LOW_MIN, RISK_HIGH_MAX

SUPA_URL = os.environ['SUPABASE_URL']
# Writes must use the SECRET key (sb_secret_…) so they bypass row-level security
# once RLS is enabled. Falls back to the publishable key until the secret is added,
# so the pipeline keeps working in the meantime.
SUPA_KEY = os.environ.get('SUPABASE_SECRET_KEY') or os.environ['SUPABASE_KEY']


def _rest(method, table, payload):
    url = f'{SUPA_URL}/rest/v1/{table}'
    body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, method=method, headers={
        'apikey': SUPA_KEY,
        'Authorization': f'Bearer {SUPA_KEY}',
        'Content-Type': 'application/json',
        'Prefer': 'resolution=merge-duplicates,return=minimal',
    })
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:300]
        print(f'  HTTP {e.code} on {table}: {body}')
        return e.code
    except Exception as e:
        print(f'  Error on {table}: {e}')
        return None


def _ts(v):
    return int(v) if v is not None else None


def _batch_upsert(table, rows, chunk=100):
    for i in range(0, len(rows), chunk):
        status = _rest('POST', table, rows[i:i+chunk])
        if status and status >= 400:
            print(f'  Batch failed at offset {i}')


BNE = 36000


def _week_mon_ts(ts):
    d = datetime.datetime.utcfromtimestamp(int(ts) + BNE)
    mon = (d - datetime.timedelta(days=d.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return int((mon - datetime.datetime(1970, 1, 1)).total_seconds()) - BNE


def _week_label(mon_ts):
    mon = datetime.datetime.utcfromtimestamp(mon_ts + BNE)
    sun = mon + datetime.timedelta(days=6)
    return f"{mon.strftime('%a %d %b')} – {sun.strftime('%a %d %b %Y')}"


def _compute_weeks(vehicles):
    veh_meta = {v['plate']: v['make'] for v in vehicles}
    weeks = {}
    for v in vehicles:
        for t in v['trips']:
            wk = _week_mon_ts(t.get('begin_ts', 0))
            weeks.setdefault(wk, {}).setdefault(v['plate'], []).append(t)
    sorted_wks = sorted(weeks.keys())
    out = []
    for i, wk_ts in enumerate(sorted_wks):
        veh_week = weeks[wk_ts]
        prev_veh = weeks.get(sorted_wks[i - 1], {}) if i > 0 else {}
        rankings = []
        for plate, trps in veh_week.items():
            km = sum(t['km'] for t in trps) or 1
            sc = round(sum(t.get('total', t.get('raw', 0)) * t['km'] for t in trps) / km, 1)  # 1 dp for tie-break display
            spd_t = [t for t in trps if t.get('spd') is not None]
            spd = round(sum(t['spd'] * t['km'] for t in spd_t) / sum(t['km'] for t in spd_t)) if spd_t else None
            prev_trps = prev_veh.get(plate, [])
            prev_sc = None
            if prev_trps:
                pkm = sum(t['km'] for t in prev_trps) or 1
                prev_sc = round(sum(t.get('total', t.get('raw', 0)) * t['km'] for t in prev_trps) / pkm)
            trend = None
            if prev_sc is not None:
                delta = sc - prev_sc
                trend = 'improving' if delta >= 3 else ('declining' if delta <= -3 else 'stable')
            rankings.append({
                'plate': plate, 'make': veh_meta.get(plate, ''), 'score': sc,
                'trips': len(trps), 'spd': spd,
                'brk': round(sum(t['brk'] * t['km'] for t in trps) / km),
                'acc': round(sum(t['acc'] * t['km'] for t in trps) / km),
                'crn': round(sum(t['crn'] * t['km'] for t in trps) / km),
                'trend': trend,
                'short': sum(1 for t in trps if t.get('km', 0) < 10),
                'std':   sum(1 for t in trps if 10 <= t.get('km', 0) < 25),
                'lng':   sum(1 for t in trps if t.get('km', 0) >= 25),
            })
        rankings.sort(key=lambda r: r['score'], reverse=True)
        for j, r in enumerate(rankings): r['rank'] = j + 1
        out.append({'ts': wk_ts, 'label': _week_label(wk_ts), 'rankings': rankings})
    return out


def _precise_avg(v):
    """km-weighted mean of trip totals, unrounded (for 1-dp display + tie-correct sort)."""
    trips = v.get('trips', [])
    km = sum(t.get('km', 0) for t in trips) or 1
    return sum(t.get('total', t.get('raw', 0)) * t.get('km', 0) for t in trips) / km


def _next_level(avg):
    if avg >= 90: return None
    if avg >= 70: return 90
    if avg >= 50: return 70
    return 50


def _enrich_vehicles(vehicles):
    """Compute rank, comp_avgs, trend and flatten for template JS.
    score_v2.py sorts vehicles but doesn't add these fields."""
    sorted_vehs = sorted(vehicles, key=_precise_avg, reverse=True)

    # Compute per-vehicle weekly scores to derive trend
    week_scores = {}  # plate -> {wk_ts -> score}
    for v in sorted_vehs:
        ws = {}
        for t in v.get('trips', []):
            wk = _week_mon_ts(t.get('begin_ts', 0))
            ws.setdefault(wk, []).append(t)
        plate_wks = {}
        for wk, trps in ws.items():
            km = sum(t['km'] for t in trps) or 1
            plate_wks[wk] = round(sum(t.get('total', t.get('raw', 0)) * t['km'] for t in trps) / km)
        week_scores[v['plate']] = plate_wks

    all_wks = sorted({wk for pw in week_scores.values() for wk in pw})

    out = []
    for i, v in enumerate(sorted_vehs):
        vv = dict(v)
        vv['rank'] = i + 1
        vv['avg'] = round(_precise_avg(v), 1)  # 1 decimal so near-ties are distinguishable

        # comp_avgs from trips
        trips = v.get('trips', [])
        km_total = sum(t.get('km', 0) for t in trips) or 1
        spd_trips = [t for t in trips if t.get('spd') is not None]
        spd_km = sum(t.get('km', 0) for t in spd_trips) or 1
        ca = {
            'spd': round(sum(t['spd'] * t['km'] for t in spd_trips) / spd_km) if spd_trips else None,
            'brk': round(sum(t.get('brk', 0) * t.get('km', 0) for t in trips) / km_total),
            'acc': round(sum(t.get('acc', 0) * t.get('km', 0) for t in trips) / km_total),
            'crn': round(sum(t.get('crn', 0) * t.get('km', 0) for t in trips) / km_total),
        }
        vv['comp_avgs'] = ca
        vv['spd_avg'] = ca['spd']
        vv['brk_avg'] = ca['brk']
        vv['acc_avg'] = ca['acc']
        vv['crn_avg'] = ca['crn']

        # Predicted best-case + next target (feeds the vehicle card's "Next level" tip).
        # Ported from fast_rerun so these exist in CI too — score_v2 doesn't compute them,
        # which previously left every vehicle defaulting to the "Top tier" message.
        valid = {k: ca[k] for k in ('spd', 'brk', 'acc', 'crn') if ca.get(k) is not None}
        worst = min(valid, key=valid.get) if valid else 'crn'
        imp = dict(ca); imp[worst] = 100
        imp_spd = imp['spd'] if imp['spd'] is not None else vv['avg']
        vv['predicted_avg'] = max(vv['avg'], round(0.45 * imp_spd + 0.20 * imp['brk']
                                                   + 0.20 * imp['crn'] + 0.15 * imp['acc']))
        vv['predicted_comp'] = worst
        vv['pred_comp'] = worst              # name the dashboard's "Focus:" tip reads
        vv['next_target'] = _next_level(vv['avg'])

        # trend: compare last two weeks
        pw = week_scores.get(v['plate'], {})
        if len(all_wks) >= 2 and len(pw) >= 2:
            cur = pw.get(all_wks[-1])
            prev = pw.get(all_wks[-2])
            if cur is not None and prev is not None:
                delta = cur - prev
                vv['trend'] = 'improving' if delta >= 3 else ('declining' if delta <= -3 else 'stable')
            else:
                vv.setdefault('trend', None)
        else:
            vv.setdefault('trend', None)

        out.append(vv)
    return out


def _date_range(vehicles):
    all_ts = [t['begin_ts'] for v in vehicles for t in v['trips'] if t.get('begin_ts')]
    if not all_ts:
        return datetime.datetime.utcnow().strftime('%d %b %Y')
    earliest = datetime.datetime.utcfromtimestamp(min(all_ts) + BNE).strftime('%d %b')
    latest = datetime.datetime.utcfromtimestamp(max(all_ts) + BNE).strftime('%d %b %Y')
    return f'{earliest} – {latest}' if earliest != latest[:6] else latest


def sync(scores_path):
    d = json.load(open(scores_path))

    # ── Vehicles ──────────────────────────────────────────────────────────
    vehs = [{'plate': v['plate'], 'make': v['make']} for v in d['vehicles']]
    _batch_upsert('vehicles', vehs)
    print(f'  vehicles: {len(vehs)} upserted')

    # ── Trips ─────────────────────────────────────────────────────────────
    trips = []
    for v in d['vehicles']:
        for t in v['trips']:
            trips.append({
                'id':             t['id'],
                'plate':          v['plate'],
                'date_str':       t.get('date', ''),
                'time_str':       t.get('t', ''),
                'km':             t.get('km', 0),
                'raw':            t.get('raw', 0),
                'spd':            t.get('spd'),        # nullable
                'brk':            t.get('brk', 0),
                'acc':            t.get('acc', 0),
                'crn':            t.get('crn', 0),
                'cov_pct':        t.get('cov_pct', 0),
                'incident':       t.get('incident', False),
                'inc_mx':         t.get('inc_mx', 0),
                'inc_dur':        t.get('inc_dur', 0),
                'inc_avg':        t.get('inc_avg', 0),
                'from_addr':      t.get('from', ''),
                'to_addr':        t.get('to', ''),
                'slat':           t.get('slat'),
                'slon':           t.get('slon'),
                'elat':           t.get('elat'),
                'elon':           t.get('elon'),
                'begin_ts':       _ts(t.get('begin_ts')),
                'end_ts':         _ts(t.get('end_ts')),
                'driving_period': t.get('driving_period', 'Day'),
                'rpm_s':          t.get('rpm_s', 0),
                'lc':             t.get('lc', False),
            })
    # Deduplicate by trip id (keep last occurrence)
    trips_dedup = {r['id']: r for r in trips}
    trips = list(trips_dedup.values())
    _batch_upsert('trips', trips)
    print(f'  trips: {len(trips)} upserted')

    # ── Incidents ─────────────────────────────────────────────────────────
    incs = []
    for inc in d.get('incidents', []):
        incs.append({
            'plate':        inc['plate'],
            'trip_ref':     inc['trip'],
            'time_str':     inc.get('time', ''),
            'date_str':     inc.get('date', ''),
            'mx':           inc.get('mx', 0),
            'dur':          inc.get('dur', 0),
            'avg_speed':    inc.get('avg', 0),
            'datetime_str': inc.get('datetime', ''),
            'speed_str':    inc.get('speed', ''),
            'loc':          inc.get('loc', ''),
            'coords':       inc.get('coords', []),
            'begin_ts':     _ts(inc.get('begin_ts')),
            'end_ts':       _ts(inc.get('end_ts')),
        })
    if incs:
        _batch_upsert('incidents', incs)
    print(f'  incidents: {len(incs)} upserted')

    # ── Trip tracks (GPS path + harsh-event locations) ────────────────────
    track_path = os.path.join(os.path.dirname(scores_path), 'track_cache.json')
    if os.path.exists(track_path):
        tc = json.load(open(track_path))
        # Cache may be keyed by composite "plate|id" (current) or bare "id" (legacy);
        # take trip_id from the value, falling back to the key's numeric tail.
        tracks = []
        for k, v in tc.items():
            tid = v.get('trip_id')
            if tid is None:
                try:
                    tid = int(str(k).split('|')[-1])
                except (ValueError, TypeError):
                    continue
            tracks.append({'trip_id': int(tid), 'plate': v.get('plate', ''),
                           'track': v.get('track', []), 'events': v.get('events', [])})
        if tracks:
            # small chunks: track payloads can be large (hundreds-1000+ GPS points each).
            # upsert merges on the (plate, trip_id) primary key.
            _batch_upsert('trip_tracks', tracks, chunk=20)
        print(f'  trip_tracks: {len(tracks)} upserted')

    # ── Fleet run record ──────────────────────────────────────────────────
    _rest('POST', 'fleet_runs', {
        'fleet_avg':     d['fleet_avg'],
        'num_vehicles':  d['num_vehicles'],
        'total_trips':   d['total_trips'],
        'num_incidents': len(d.get('incidents', [])),
    })
    print(f'  fleet_runs: recorded (avg {d["fleet_avg"]})')

    # ── latest_run (live dashboard snapshot) ──────────────────────────────
    weeks = _compute_weeks(d['vehicles'])
    date_range = _date_range(d['vehicles'])
    # fleet average to 1 dp (mean of per-vehicle precise averages)
    _vavgs = [_precise_avg(v) for v in d['vehicles']]
    fleet_avg_1dp = round(sum(_vavgs) / len(_vavgs), 1) if _vavgs else d['fleet_avg']
    now_iso = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

    # Fleet insight (weekly-cached: component stars + risk + AI summary). Embed the
    # display fields if fleet_insight.py has produced a cache; omit otherwise.
    fleet_insight = None
    veh_summaries = {}
    fi_path = os.path.join(os.path.dirname(scores_path), 'fleet_insight_cache.json')
    if os.path.exists(fi_path):
        fi = json.load(open(fi_path))
        veh_summaries = fi.get('vehicles') or {}  # weekly per-vehicle AI summaries, by plate
        # Component stars, trip mix, risk and the fleet average are recomputed LIVE here so
        # the panel always agrees with the headline; only the AI prose + its week are weekly.
        comps = _fleet_components(d['vehicles'])
        mix = _trip_mix(d['vehicles'])
        live_risk, _ = _risk(fleet_avg_1dp)
        fleet_insight = {
            'spd': comps['spd'], 'brk': comps['brk'], 'acc': comps['acc'], 'crn': comps['crn'],
            'short': mix['short'], 'standard': mix['standard'], 'long': mix['long'],
            'fleet_avg': fleet_avg_1dp, 'risk': live_risk,
            'risk_low_min': RISK_LOW_MIN, 'risk_high_max': RISK_HIGH_MAX,
            'summary': fi.get('summary'), 'week_of': fi.get('week_of'),
        }
        print(f'  fleet_insight: embedded (prose week of {fi.get("week_of")}, '
              f'live risk {live_risk} @ {fleet_avg_1dp})')

    # Enrich vehicles, then attach the weekly per-vehicle AI summary (overrides any template).
    enriched = _enrich_vehicles(d['vehicles'])
    n_veh_sum = 0
    for vv in enriched:
        s = (veh_summaries.get(vv['plate']) or {}).get('summary')
        if s:
            vv['summary'] = s
            n_veh_sum += 1
    print(f'  vehicle summaries: {n_veh_sum}/{len(enriched)} attached')

    _rest('POST', 'latest_run', {
        'id': 1,
        'updated_at': now_iso,
        'data': {
            'vehicles':      enriched,
            'incidents':     d.get('incidents', []),
            'weeks':         weeks,
            'fleet_avg':     fleet_avg_1dp,
            'num_vehicles':  d['num_vehicles'],
            'total_trips':   d['total_trips'],
            'fleet_trend':   d.get('fleet_trend', 0),
            'date_range':    date_range,
            'fleet_insight': fleet_insight,
        }
    })
    print(f'  latest_run: updated')


if __name__ == '__main__':
    BASE = os.path.dirname(os.path.abspath(__file__))
    print('Syncing to Supabase...')
    sync(os.path.join(BASE, 'scores_only.json'))
    print('Done.')
