import json, os
from dotenv import load_dotenv

BASE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE, '.env'))
SCORES = os.path.join(BASE, 'scores_only.json')
GEO    = os.path.join(BASE, 'geo_cache.json')
if not os.path.exists(SCORES):
    SCORES = '/tmp/scores_only.json'
if not os.path.exists(GEO):
    GEO = '/tmp/geo_cache.json'

with open(SCORES) as f:
    d = json.load(f)

# Apply geocoding cache — auto-geocode any missing coords via Photon
import urllib.request as _ur, time as _time

def _rev(lat, lon):
    try:
        url = f'https://photon.komoot.io/reverse?lat={lat}&lon={lon}'
        req = _ur.Request(url, headers={'User-Agent':'Telemax-VSS/1.0'})
        with _ur.urlopen(req, timeout=7) as r:
            p = __import__('json').load(r).get('features',[{}])[0].get('properties',{})
        road = p.get('street') or p.get('name') or ''
        sub  = p.get('district') or p.get('suburb') or p.get('city') or ''
        return ', '.join(x for x in [road, sub] if x)
    except: return ''

cache = json.load(open(GEO)) if os.path.exists(GEO) else {}
new_coords = []
for v in d['vehicles']:
    for t in v['trips']:
        for field, lk, lok in [('from','slat','slon'),('to','elat','elon')]:
            if t.get(lk) and t.get(lok):
                key = f'{round(t[lk],3)},{round(t[lok],3)}'
                if key not in cache or not cache[key]:
                    new_coords.append(key)

for key in set(new_coords):
    lat, lon = key.split(',')
    cache[key] = _rev(float(lat), float(lon))
    print(f'  Geocoded {key} -> {cache[key]}')

if new_coords:
    json.dump(cache, open(GEO,'w'))

for v in d['vehicles']:
    for t in v['trips']:
        for field, lk, lok in [('from','slat','slon'),('to','elat','elon')]:
            if not t.get(field) and t.get(lk) and t.get(lok):
                key = f'{round(t[lk],3)},{round(t[lok],3)}'
                t[field] = cache.get(key, '')

def jb(b): return 'true' if b else 'false'
def jv(v): return 'null' if v is None else str(v)
def js(s): return (s or '').replace('\\', '\\\\').replace('"', '\\"').replace("'", "\\'")

veh_js = 'const vehicles=[\n'
for v in d['vehicles']:
    ca = v.get('comp_avgs',{})
    veh_js += ('  {plate:"' + v['plate'] + '",make:"' + v['make'] + '",avg:' + str(v['avg']) +
               ',inc:' + jb(v['inc']) + ',low_cov:' + jb(v.get('low_cov',False)) +
               ',avg_cov:' + str(v.get('avg_cov',0)) +
               ',rank:' + str(v.get('rank',0)) +
               ',predicted:' + str(v.get('predicted_avg', v['avg'])) +
               ',pred_comp:"' + js(v.get('predicted_comp','')) + '"' +
               ',trend:"' + js(v.get('trend','') or '') + '",next_target:' + (str(v['next_target']) if v.get('next_target') else 'null') +
               ',spd_avg:' + (str(ca.get('spd')) if ca.get('spd') is not None else 'null') +
               ',brk_avg:' + str(ca.get('brk',0)) +
               ',acc_avg:' + str(ca.get('acc',0)) +
               ',crn_avg:' + str(ca.get('crn',0)) +
               ',summary:"' + js(v.get('summary','')) + '",trips:[\n')
    for t in v['trips']:
        veh_js += ('    {id:' + str(t['id']) + ',date:"' + js(t.get('date','')) + '",t:"' + t['t'] + '",km:' + str(t['km']) +
                   ',lc:' + jb(t['lc']) + ',spd:' + jv(t['spd']) +
                   ',brk:' + str(t['brk']) + ',acc:' + str(t['acc']) + ',crn:' + str(t['crn']) +
                   ',raw:' + str(t.get('raw', t['total'])) + ',total:' + str(t.get('total', t.get('raw', 0))) + ',cov:' + str(t.get('cov_pct',0)) +
                   ',incident:' + jb(t['incident']) +
                   ',from:"' + js(t.get('from','')) + '",to:"' + js(t.get('to','')) + '",dp:"' + js(t.get('driving_period','Day')) +
                   '",slat:' + (str(t['slat']) if t.get('slat') is not None else 'null') +
                   ',slon:' + (str(t['slon']) if t.get('slon') is not None else 'null') +
                   ',elat:' + (str(t['elat']) if t.get('elat') is not None else 'null') +
                   ',elon:' + (str(t['elon']) if t.get('elon') is not None else 'null') + ',rpm_s:' + str(t.get('rpm_s',0)) + '},\n')
    veh_js += '  ]},\n'
veh_js += '];\n'

inc_js = 'const incData=[\n'
for inc in d['incidents']:
    coords = inc.get('coords', [])
    coords_js = '[' + ','.join(str(c) for c in coords) + ']' if coords else '[]'
    inc_js += ('  {plate:"' + inc['plate'] + '",make:"' + inc['make'] + '",' +
               'trip:"' + inc['trip'] + '",time:"' + inc['time'] + '",' +
               'mx:' + str(inc['mx']) + ',dur:' + str(inc['dur']) + ',avg:' + str(inc['avg']) + ',' +
               'datetime:"' + js(inc.get('datetime','')) + '",' +
               'speed:"' + js(inc.get('speed','')) + '",' +
               'loc:"' + js(inc.get('loc','')) + '",' +
               'coords:' + coords_js + '},\n')
inc_js += '];\n'

import datetime as dt

# Compute date range from trip data
BNE = 36000
all_begins = [t['begin_ts'] for v in d['vehicles'] for t in v['trips'] if t.get('begin_ts')]
if all_begins:
    earliest = dt.datetime.utcfromtimestamp(min(all_begins)+BNE).strftime('%d %b')
    latest   = dt.datetime.utcfromtimestamp(max(all_begins)+BNE).strftime('%d %b %Y')
    date_range = f'{earliest} – {latest}' if earliest != latest.split(' ')[0]+' '+latest.split(' ')[1] else latest
else:
    date_range = dt.datetime.utcnow().strftime('%d %b %Y')

template = open(os.path.join(BASE, 'combined_template.html'), encoding='utf-8').read()

# Fleet trend indicator
trend_val = d.get('fleet_trend', 0)
if trend_val > 0:
    fleet_trend_html = f'<span style="font-size:10px;font-weight:600;color:var(--success);">&#8679; +{trend_val}</span>'
elif trend_val < 0:
    fleet_trend_html = f'<span style="font-size:10px;font-weight:600;color:var(--danger);">&#8681; {trend_val}</span>'
else:
    fleet_trend_html = '<span style="font-size:10px;color:var(--text3);">&#8594; stable</span>'

# Build leaderboard weeks JS
def week_monday_ts(ts):
    d2 = dt.datetime.utcfromtimestamp(ts + BNE)
    dow = d2.weekday()
    mon = (d2 - dt.timedelta(days=dow)).replace(hour=0, minute=0, second=0, microsecond=0)
    return int((mon - dt.datetime(1970,1,1)).total_seconds()) - BNE

def week_label(mon_ts):
    mon = dt.datetime.utcfromtimestamp(mon_ts + BNE)
    sun = mon + dt.timedelta(days=6)
    return f"{mon.strftime('%a %d %b')} – {sun.strftime('%a %d %b %Y')}"

weeks = {}
for v in d['vehicles']:
    for t in v['trips']:
        wk = week_monday_ts(t.get('begin_ts', 0))
        weeks.setdefault(wk, {}).setdefault(v['plate'], []).append(t)

def score_trips(trips):
    if not trips: return None
    km = sum(t['km'] for t in trips) or 1
    return round(sum(t.get('total', t['raw'])*t['km'] for t in trips)/km)

def comp_avgs_trips(trips):
    km = sum(t['km'] for t in trips) or 1
    spd_t = [t for t in trips if t.get('spd') is not None]
    return {
        'spd': round(sum(t['spd']*t['km'] for t in spd_t)/sum(t['km'] for t in spd_t)) if spd_t else None,
        'brk': round(sum(t['brk']*t['km'] for t in trips)/km),
        'acc': round(sum(t['acc']*t['km'] for t in trips)/km),
        'crn': round(sum(t['crn']*t['km'] for t in trips)/km),
    }

veh_meta = {v['plate']: v['make'] for v in d['vehicles']}
sorted_weeks = sorted(weeks.keys())
weeks_out = []
for i, wk_ts in enumerate(sorted_weeks):
    veh_week = weeks[wk_ts]
    prev_veh = weeks.get(sorted_weeks[i-1], {}) if i > 0 else {}
    rankings = []
    for plate, trips in veh_week.items():
        sc2 = score_trips(trips)
        if sc2 is None: continue
        ca = comp_avgs_trips(trips)
        prev_sc = score_trips(prev_veh.get(plate, []))
        if prev_sc is not None:
            delta = sc2 - prev_sc
            trend2 = 'improving' if delta >= 3 else ('declining' if delta <= -3 else 'stable')
        else:
            trend2 = None
        rankings.append({'plate': plate, 'make': veh_meta.get(plate,''), 'score': sc2,
                         'trips': len(trips), **ca, 'trend': trend2})
    rankings.sort(key=lambda x: x['score'], reverse=True)
    for j,r in enumerate(rankings): r['rank'] = j+1
    weeks_out.append({'ts': wk_ts, 'label': week_label(wk_ts), 'rankings': rankings})

def jsv(v): return 'null' if v is None else str(v)
def jst(v): return 'null' if v is None else f'"{v}"'

weeks_js = 'const weeksData=[\n'
for wk in weeks_out:
    weeks_js += f'  {{ts:{wk["ts"]},label:"{wk["label"]}",rankings:[\n'
    for r in wk['rankings']:
        weeks_js += (f'    {{rank:{r["rank"]},plate:"{r["plate"]}",make:"{r["make"]}",'
                     f'score:{r["score"]},trips:{r["trips"]},'
                     f'spd:{jsv(r["spd"])},brk:{jsv(r["brk"])},acc:{jsv(r["acc"])},crn:{jsv(r["crn"])},'
                     f'trend:{jst(r["trend"])}}},\n')
    weeks_js += '  ]},\n'
weeks_js += '];\n'

html = (template
    .replace('__FLEET_AVG__', str(d['fleet_avg']))
    .replace('__NUM_VEHICLES__', str(d['num_vehicles']))
    .replace('__TOTAL_TRIPS__', str(d['total_trips']))
    .replace('__NUM_INCIDENTS__', str(len(d['incidents'])))
    .replace('__DATE_RANGE__', date_range)
    .replace('__FLEET_TREND__', fleet_trend_html)
    .replace('__VEH_JS__', veh_js)
    .replace('__INC_JS__', inc_js)
    .replace('__WEEKS_JS__', weeks_js)
    .replace('__SUPA_URL__', os.environ.get('SUPABASE_URL', ''))
    .replace('__SUPA_KEY__', os.environ.get('SUPABASE_KEY', ''))
)

os.makedirs(os.path.join(BASE, 'docs'), exist_ok=True)
out = os.path.join(BASE, 'docs', 'index.html')
with open(out, 'w', encoding='utf-8') as f:
    f.write(html)
print(f'Built: {out}')
