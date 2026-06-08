"""
Builds DriveIQ Weekly Leaderboard from scores_only.json.
Groups trips into Mon–Sun calendar weeks (AEST), computes per-vehicle scores,
produces DriveIQ_Leaderboard.html in the outputs folder.
"""
import json, os, datetime

BASE   = os.path.dirname(os.path.abspath(__file__))
SCORES = os.path.join(BASE, 'scores_only.json')
BNE    = 36000  # AEST offset seconds

def week_monday_ts(ts):
    """Return UTC unix ts of Monday 00:00 AEST for the week containing ts."""
    dt = datetime.datetime.utcfromtimestamp(ts + BNE)
    dow = dt.weekday()
    mon = (dt - datetime.timedelta(days=dow)).replace(hour=0, minute=0, second=0, microsecond=0)
    return int((mon - datetime.datetime(1970, 1, 1)).total_seconds()) - BNE

def week_label(mon_ts):
    mon = datetime.datetime.utcfromtimestamp(mon_ts + BNE)
    sun = mon + datetime.timedelta(days=6)
    return f"{mon.strftime('%a %d %b')} – {sun.strftime('%a %d %b %Y')}"

def score_from_trips(trips):
    """Compute km-weighted average score from a list of trip dicts."""
    if not trips: return None
    km_total = sum(t['km'] for t in trips) or 1
    return round(sum(t.get('total', t['raw']) * t['km'] for t in trips) / km_total)

def comp_avgs(trips):
    if not trips: return {}
    km_total = sum(t['km'] for t in trips) or 1
    spd_t = [t for t in trips if t.get('spd') is not None]
    spd = round(sum(t['spd']*t['km'] for t in spd_t) / sum(t['km'] for t in spd_t)) if spd_t else None
    return {
        'spd': spd,
        'brk': round(sum(t['brk']*t['km'] for t in trips) / km_total),
        'acc': round(sum(t['acc']*t['km'] for t in trips) / km_total),
        'crn': round(sum(t['crn']*t['km'] for t in trips) / km_total),
    }

with open(SCORES) as f:
    d = json.load(f)

# --- Group trips by week and vehicle ---
# weeks: {mon_ts: {plate: [trips...]}}
weeks = {}
veh_meta = {v['plate']: v['make'] for v in d['vehicles']}

for v in d['vehicles']:
    for t in v['trips']:
        wk = week_monday_ts(t.get('begin_ts', 0))
        weeks.setdefault(wk, {}).setdefault(v['plate'], []).append(t)

sorted_weeks = sorted(weeks.keys())

# --- Build per-week rankings with trend vs prior week ---
weeks_out = []
for i, wk_ts in enumerate(sorted_weeks):
    veh_week = weeks[wk_ts]
    prev_wk = sorted_weeks[i-1] if i > 0 else None
    prev_veh = weeks.get(prev_wk, {}) if prev_wk else {}

    rankings = []
    for plate, trips in veh_week.items():
        sc = score_from_trips(trips)
        if sc is None: continue
        ca = comp_avgs(trips)

        # trend vs prior week
        if plate in prev_veh:
            prev_sc = score_from_trips(prev_veh[plate])
            delta = sc - prev_sc if prev_sc is not None else 0
            trend = 'improving' if delta >= 3 else ('declining' if delta <= -3 else 'stable')
        else:
            trend = None

        rankings.append({
            'plate': plate,
            'make': veh_meta.get(plate, ''),
            'score': sc,
            'trips': len(trips),
            'spd': ca.get('spd'),
            'brk': ca.get('brk'),
            'acc': ca.get('acc'),
            'crn': ca.get('crn'),
            'trend': trend,
        })

    rankings.sort(key=lambda x: x['score'], reverse=True)
    for j, r in enumerate(rankings): r['rank'] = j + 1

    weeks_out.append({
        'ts': wk_ts,
        'label': week_label(wk_ts),
        'rankings': rankings,
    })

# --- Serialize to JS ---
def js(s): return (s or '').replace('\\','\\\\').replace('"','\\"').replace("'","\\'")
def jv(v): return 'null' if v is None else str(v)
def jb(b): return 'true' if b else 'false'

weeks_js = 'const weeksData=[\n'
for wk in weeks_out:
    weeks_js += f'  {{ts:{wk["ts"]},label:"{js(wk["label"])}",rankings:[\n'
    for r in wk['rankings']:
        weeks_js += (
            f'    {{rank:{r["rank"]},plate:"{js(r["plate"])}",make:"{js(r["make"])}",'
            f'score:{r["score"]},trips:{r["trips"]},'
            f'spd:{jv(r["spd"])},brk:{jv(r["brk"])},acc:{jv(r["acc"])},crn:{jv(r["crn"])},'
            f'trend:{("null" if r["trend"] is None else chr(34)+r["trend"]+chr(34))}}},\n'
        )
    weeks_js += '  ]},\n'
weeks_js += '];\n'

template_path = os.path.join(BASE, 'leaderboard_template.html')
template = open(template_path).read()
html = template.replace('__WEEKS_JS__', weeks_js)

out = os.path.join(BASE, '..', 'DriveIQ_Leaderboard.html')
with open(out, 'w') as f:
    f.write(html)

print(f'Leaderboard built — {len(weeks_out)} weeks, latest: {weeks_out[-1]["label"]} ({len(weeks_out[-1]["rankings"])} vehicles)')
