"""
Fast rerun - skips slow Flespi night-interval re-fetch and re-uses incident cache.
Scoring, geocoding, and HTML build only.
"""
import json, os, sys, datetime, urllib.request

BASE   = os.path.dirname(os.path.abspath(__file__))
SCORES = os.path.join(BASE, 'scores_only.json')
GEO    = os.path.join(BASE, 'geo_cache.json')
INC    = os.path.join(BASE, 'incident_cache.json')

BNE   = 36000
CUTOFF = 1780322400
TREND = {'improving':'Recent trips show a clear upward trend — this driver is actively getting better.',
         'declining':'Recent trips show a downward trend — performance has slipped and warrants attention.',
         'stable':'Performance has been consistent across trips with no significant change in trend.'}
WNAMES = {'spd':'speeding','brk':'braking','acc':'acceleration','crn':'cornering'}

def rev_geo(lat, lon):
    try:
        req = urllib.request.Request(f'https://photon.komoot.io/reverse?lat={lat}&lon={lon}',
            headers={'User-Agent':'DriveIQ/1.0'})
        with urllib.request.urlopen(req, timeout=7) as r:
            p = json.load(r).get('features',[{}])[0].get('properties',{})
        road = p.get('street') or p.get('name') or ''
        sub  = p.get('district') or p.get('suburb') or p.get('city') or ''
        return ', '.join(x for x in [road, sub] if x)
    except: return ''

def next_level(avg):
    if avg>=90: return None
    if avg>=70: return 90
    if avg>=50: return 70
    return 50

# ── STEP 1: Load fresh /tmp data ──────────────────────────────────────────────
print('Step 1: Loading fresh data...')
with open(SCORES) as f: d = json.load(f)

# Preserve driving_period from previous cached scores (avoids Flespi re-fetch)
prev_dp = {}
if os.path.exists(SCORES):
    try:
        prev = json.load(open(SCORES))
        for v in prev.get('vehicles',[]):
            for t in v.get('trips',[]):
                prev_dp[t['id']] = t.get('driving_period','Day')
    except: pass

# ── STEP 2: Filter to Jun 2+ ──────────────────────────────────────────────────
print('Step 2: Filtering to Jun 2+...')
for v in d['vehicles']:
    v['trips'] = [t for t in v['trips'] if t.get('begin_ts',0) >= CUTOFF]
d['vehicles'] = [v for v in d['vehicles'] if v['trips']]

# ── STEP 3: Restore driving_period from cache ──────────────────────────────────
print('Step 3: Restoring driving periods from cache...')
for v in d['vehicles']:
    for t in v['trips']:
        t['driving_period'] = prev_dp.get(t['id'], 'Day')

# ── STEP 4: Post-process vehicles ─────────────────────────────────────────────
print('Step 4: Calculating scores, trends, summaries...')
for v in d['vehicles']:
    trips = v['trips']; km_total = sum(t['km'] for t in trips) or 1
    v['avg'] = round(sum(t.get('total', t['raw'])*t['km'] for t in trips)/km_total)
    spd_t = [t for t in trips if t['spd'] is not None]
    spd_avg = round(sum(t['spd']*t['km'] for t in spd_t)/sum(t['km'] for t in spd_t)) if spd_t else None
    v['comp_avgs'] = {'spd':spd_avg,
        'brk':round(sum(t['brk']*t['km'] for t in trips)/km_total),
        'acc':round(sum(t['acc']*t['km'] for t in trips)/km_total),
        'crn':round(sum(t['crn']*t['km'] for t in trips)/km_total)}
    ca = v['comp_avgs']
    worst = min((k for k in ca if ca[k] is not None), key=lambda k: ca.get(k,100))
    imp = {**ca, worst:100}; imp_spd = imp['spd'] if imp['spd'] is not None else v['avg']
    v['predicted_avg'] = max(v['avg'], round(0.45*imp_spd+0.20*imp['brk']+0.20*imp['crn']+0.15*imp['acc']))
    v['predicted_comp'] = worst; v['next_target'] = next_level(v['avg'])
    scores = [t['raw'] for t in trips]; n = len(scores)
    if n>=4: mid=n//2; delta=sum(scores[:mid])/mid-sum(scores[mid:])/(n-mid); trend='improving' if delta>=3 else('declining' if delta<=-3 else 'stable')
    elif n>=2: delta=scores[0]-scores[-1]; trend='improving' if delta>=3 else('declining' if delta<=-3 else 'stable')
    else: trend=None
    v['trend'] = trend
    avg = v['avg']; inc_count = sum(1 for t in trips if t['incident'])
    if avg>=90: s1="This vehicle is delivering outstanding driving behaviour with strong performance across all components."
    elif avg>=70: s1=f"The {v['make']} is driving well overall. {WNAMES.get(worst,'').capitalize()} is the main area with room to improve."
    elif avg>=50: s1=f"The {v['make']} has clear room to improve. {WNAMES.get(worst,'Speeding').capitalize()} is the primary issue pulling the score down."
    else: s1=f"The {v['make']} requires significant attention. {WNAMES.get(worst,'Speeding').capitalize()} is consistently the main concern and needs to be addressed."
    s2 = (f"{inc_count} confirmed speed incident{'s' if inc_count>1 else ''} recorded this period. " if inc_count else "No confirmed speed incidents this period. ")
    s2 += TREND.get(trend,'')
    v['summary'] = s1.strip()+' '+s2.strip()

d['vehicles'].sort(key=lambda v:v['avg'],reverse=True)
for i,v in enumerate(d['vehicles']): v['rank']=i+1
d['total_trips']=sum(len(v['trips']) for v in d['vehicles'])
d['fleet_avg']=round(sum(v['avg'] for v in d['vehicles'])/len(d['vehicles']))
d['num_vehicles']=len(d['vehicles'])

# ── STEP 5: Apply incident cache ──────────────────────────────────────────────
print('Step 5: Applying incident cache...')
inc_cache = json.load(open(INC)) if os.path.exists(INC) else {}
for inc in d['incidents']:
    cache_key = inc['plate']+'_'+inc['trip'].replace('#','')
    if cache_key in inc_cache:
        cached = inc_cache[cache_key]
        inc.update({k:cached[k] for k in ['datetime','speed','loc','coords'] if k in cached})

# ── STEP 6: Geocode (new coords only) ─────────────────────────────────────────
print('Step 6: Geocoding new addresses...')
geo_cache = json.load(open(GEO)) if os.path.exists(GEO) else {}
missing = set()
for v in d['vehicles']:
    for t in v['trips']:
        for lk,lok in [('slat','slon'),('elat','elon')]:
            if t.get(lk) and t.get(lok):
                key=f'{round(t[lk],3)},{round(t[lok],3)}'
                if key not in geo_cache or not geo_cache[key]: missing.add(key)
print(f'  {len(missing)} new coordinates to geocode')
for key in missing:
    lat,lon=key.split(','); geo_cache[key]=rev_geo(float(lat),float(lon))
    print(f'  Geocoded {key} → {geo_cache[key]}')
if missing: json.dump(geo_cache, open(GEO,'w'))
for v in d['vehicles']:
    for t in v['trips']:
        for field,lk,lok in [('from','slat','slon'),('to','elat','elon')]:
            if not t.get(field) and t.get(lk) and t.get(lok):
                t[field]=geo_cache.get(f'{round(t[lk],3)},{round(t[lok],3)}','')

# ── STEP 7: Save and build HTML ───────────────────────────────────────────────
print('Step 7: Saving and building HTML...')
hist_file = os.path.join(BASE, 'fleet_history.json')
prev_avg = json.load(open(hist_file)).get('fleet_avg') if os.path.exists(hist_file) else None
d['fleet_trend'] = (d['fleet_avg'] - prev_avg) if prev_avg is not None else 0
json.dump({'fleet_avg': d['fleet_avg']}, open(hist_file,'w'))
json.dump(d, open(SCORES,'w'))

import subprocess
result = subprocess.run([sys.executable, os.path.join(BASE,'build_html.py')], capture_output=True, text=True)
print(result.stdout.strip())
if result.returncode != 0:
    print('HTML build error:', result.stderr[:500])

result2 = subprocess.run([sys.executable, os.path.join(BASE,'build_leaderboard.py')], capture_output=True, text=True)
print(result2.stdout.strip())
if result2.returncode != 0:
    print('Leaderboard build error:', result2.stderr[:500])

# ── STEP 7b: Extract per-trip GPS tracks + harsh-event locations ──────────────
print('Step 7b: Extracting trip tracks + event locations...')
result_tt = subprocess.run([sys.executable, os.path.join(BASE,'trip_tracks.py')], capture_output=True, text=True)
print(result_tt.stdout.strip())
if result_tt.returncode != 0:
    print('Trip tracks error:', result_tt.stderr[:500])

# ── STEP 8: Sync to Supabase ──────────────────────────────────────────────
print('Step 8: Syncing to Supabase...')
result3 = subprocess.run([sys.executable, os.path.join(BASE,'supabase_sync.py')], capture_output=True, text=True)
print(result3.stdout.strip())
if result3.returncode != 0:
    print('Supabase sync error:', result3.stderr[:500])

print(f'\n✓ Done — {d["num_vehicles"]} vehicles, {d["total_trips"]} trips, fleet avg {d["fleet_avg"]}, {len(d["incidents"])} incidents')
for v in d['vehicles']:
    print(f'  #{v["rank"]:2} {v["plate"]:10} {v["avg"]:3}  {len(v["trips"])}t')
