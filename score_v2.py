import urllib.request, json, datetime, os
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))

TOKEN = os.environ['FLESPI_TOKEN']
# Telemax's scoring calc (the original single-tenant default). Other companies
# carry their own calc id in the Supabase `companies` table.
TELEMAX_CALC = os.environ.get('FLESPI_CALC_ID', '2923614')
BNE=36000
# Data window starts 2 Jun 2026 00:00 AEST (absolute floor — no data before this)
TODAY = int((datetime.datetime(2026, 6, 2) - datetime.datetime(1970, 1, 1)).total_seconds()) - BNE
# Rolling scoring window. A cumulative all-time mean gave scores enormous
# inertia: a vehicle with ~4,300km of history at 63 needed ~11,500km of flawless
# driving (2.7x its whole history, ~a year) just to reach 90, so real improvement
# was invisible for months. Only the last WINDOW_DAYS now count.
WINDOW_DAYS = int(os.environ.get('DRIVEIQ_WINDOW_DAYS', '30'))
_NOW   = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
CUTOFF = max(TODAY, _NOW - WINDOW_DAYS * 86400)
# Minimum exposure before a score is treated as established. Without this a
# vehicle with one 5km trip shows a headline score that a single trip can swing
# by 50 points.
MIN_KM, MIN_TRIPS = 200, 3
# Telemax fleet (curated plate/make map — keeps Telemax output identical).
DEVS={6536476:'856KZ4',6605289:'387BX3',6605473:'627KB5',6613713:'136YSI',6711239:'570RSO',6884310:'534JDZ',6884322:'934NQ4',6884325:'WHOOP',7419562:'476NP5',7585062:'344IT2',7734421:'VolvoXC60',7734429:'873BX8',8180103:'498IO7'}
MAKES={'856KZ4':'Toyota Hilux','387BX3':'GWM Cannon','627KB5':'BYD Seal','136YSI':'Hyundai Elantra','570RSO':'Subaru Forester','534JDZ':'Honda Jazz','934NQ4':'Ford Ranger','WHOOP':'Ford Ranger','476NP5':'Nissan Murano','344IT2':'Ford Ranger','VolvoXC60':'Volvo XC60','873BX8':'Hyundai i30','498IO7':'Toyota Corolla'}

def sub(p,g,b): return 100 if p<=g else (0 if p>=b else round(100*(1-(p-g)/(b-g))))

def score_trip(t, fleet_mean):
    m=t.get('moving_time_s',1) or 1
    cv=t.get('wialon_coverage_s',0); ss=t.get('speeding_time_s',0); sx=t.get('speeding_excess_kmh_s',0)
    km=round(t.get('mileage_km',0))
    brk=sub(t.get('harsh_braking_time_s',0)/m*100,0,2)
    acc=sub(t.get('harsh_accel_time_s',0)/m*100,0,2)
    crn=sub(t.get('harsh_cornering_time_s',0)/m*100,0,7)
    cp=min(100,cv/m*100) if m>0 else 0
    spd_w=0.45*min(1.0,cp/70); rem=1.0-spd_w
    brk_w=0.20*(rem/0.55); crn_w=0.20*(rem/0.55); acc_w=0.15*(rem/0.55)
    if cv>=30:
        spd=sub(sx/cv,0,1.0); _wsum=spd_w*spd+brk_w*brk+crn_w*crn+acc_w*acc
    else:
        spd=None; _wsum=brk_w*brk+crn_w*crn+acc_w*acc
    # No per-trip shrink (components already distance-normalized). raw stays integer
    # for the DB column; total carries 1 dp for display + km-weighted averages.
    raw=round(_wsum); total=round(_wsum,1)
    mx=t.get('max_speed_over_limit_kmh',0); ex=t.get('speeding_excess_kmh_s',0)
    dt=datetime.datetime.utcfromtimestamp(t['begin']+BNE)
    sl=t.get('start_location',{}); el=t.get('end_location',{})
    return {'id':t['id'],'date':dt.strftime('%a %d %b'),'t':dt.strftime('%H:%M')+'→'+datetime.datetime.utcfromtimestamp(t['end']+BNE).strftime('%H:%M'),
            'km':km,'lc':cp<1,'spd':spd,'brk':brk,'acc':acc,'crn':crn,'raw':raw,'total':total,'rpm_s':round(t.get('high_rpm_time_s',0)),
            'incident':mx>=20 and ss>=30 and cp>=70,
            'inc_mx':round(mx),'inc_dur':round(ss),'inc_avg':round(ex/ss,1) if ss>0 else 0,
            'cov_pct':round(cp),'begin_ts':t['begin'],'end_ts':t['end'],
            'slat':sl.get('position.latitude'),'slon':sl.get('position.longitude'),
            'elat':el.get('position.latitude'),'elon':el.get('position.longitude'),'from':'','to':''}

def fetch(calc, d):
    req=urllib.request.Request(f'https://flespi.io/gw/calcs/{calc}/devices/{d}/intervals/all',
        data=json.dumps({'count':200,'reverse':True}).encode(),
        headers={'Authorization':f'FlespiToken {TOKEN}','Content-Type':'application/json'},method='GET')
    with urllib.request.urlopen(req,timeout=12) as r: return json.load(r).get('result',[])

FLEET_MEAN=88

def score_fleet(calc, devs, makes):
    """Score one company's fleet. Identical logic to the original single-tenant
    pipeline, parameterised by calc id + device/plate/make maps."""
    raw_intervals={}
    for did,pl in devs.items():
        ints=[t for t in fetch(calc,did) if t.get('begin',0)>=CUTOFF and t.get('mileage_km',0)>=3.0 and t.get('moving_time_s',0)>=240]
        raw_intervals[did]=(pl,ints)
    veh=[]; inc=[]
    for did,(pl,ints) in raw_intervals.items():
        trips=[score_trip(t,FLEET_MEAN) for t in ints]
        # Only keep "full-data" trips: speeding must have been measurable (spd is
        # not None, i.e. road speed-limit coverage >= 30s), so all four behaviours
        # are scored. Excludes un-enriched trips that would otherwise read a
        # misleading perfect 100.
        trips=[t for t in trips if t is not None and t.get('spd') is not None]
        if not trips: continue
        km_total=sum(t['km'] for t in trips)
        avg=round(sum(t['total']*t['km'] for t in trips)/km_total) if km_total>0 else 0
        cov_trips=[t for t in trips if t['cov_pct']>0]
        avg_cov=round(sum(t['cov_pct'] for t in cov_trips)/len(cov_trips)) if cov_trips else 0
        low_cov=avg_cov<60 and len(cov_trips)>0
        # Not enough driving yet for the score to mean much (one trip could swing it wildly).
        provisional = km_total < MIN_KM or len(trips) < MIN_TRIPS
        veh.append({'plate':pl,'make':makes.get(pl,''),'avg':avg,'inc':any(t['incident'] for t in trips),
                    'avg_cov':avg_cov,'low_cov':low_cov,'km_total':round(km_total),
                    'provisional':provisional,'trips':trips})
        [inc.append({'plate':pl,'make':makes.get(pl,''),'trip':f'#{t["id"]}','time':t['t'],'date':t['date'],
                     'mx':t['inc_mx'],'dur':t['inc_dur'],'avg':t['inc_avg'],
                     'begin_ts':t['begin_ts'],'end_ts':t['end_ts'],'dev_id':did,
                     'datetime':'','speed':'','loc':'','coords':[]})
         for t in trips if t['incident']]
    veh.sort(key=lambda x:x['avg'],reverse=True); inc.sort(key=lambda x:x['mx'],reverse=True)
    # Fleet average from established vehicles only, so a one-trip vehicle can't
    # swing it (falls back to all vehicles while none have enough exposure yet).
    _base=[v for v in veh if not v['provisional']] or veh
    fa=round(sum(v['avg'] for v in _base)/len(_base)) if _base else 0
    return {'vehicles':veh,'incidents':inc,'fleet_avg':fa,'total_trips':sum(len(v['trips']) for v in veh),
            'generated':datetime.datetime.utcnow().strftime('%d %b %Y %H:%M UTC'),'num_vehicles':len(veh)}

# ── Company roster ──────────────────────────────────────────────────────────
# Telemax uses its curated DEVS/MAKES; every other company is read from the
# Supabase `companies` table and its devices are taken from its Flespi calc's
# own device assignment (source of truth), with plate = device name.
def _supa_get(path):
    url = os.environ['SUPABASE_URL'].rstrip('/') + '/rest/v1/' + path
    key = os.environ.get('SUPABASE_SECRET_KEY') or os.environ['SUPABASE_KEY']
    req = urllib.request.Request(url, headers={'apikey':key, 'Authorization':f'Bearer {key}'})
    with urllib.request.urlopen(req, timeout=20) as r: return json.load(r)

def _flespi_get(path):
    req = urllib.request.Request('https://flespi.io'+path, headers={'Authorization':f'FlespiToken {TOKEN}'})
    with urllib.request.urlopen(req, timeout=40) as r: return json.load(r).get('result', [])

_MAKE_FIX = {'MERCEDES-BEN': 'Mercedes-Benz', 'MERCEDES-BENZ': 'Mercedes-Benz'}
def _fmt_token(w):
    # Keep alphanumeric model codes uppercase (CX-3, GLE300D, I30, E5); title-case words.
    return w.upper() if any(c.isdigit() for c in w) else w.title()
def _fmt_make_model(mk, mdl):
    mk = (mk or '').strip(); mdl = (mdl or '').strip()
    mk_f = _MAKE_FIX.get(mk.upper()) or ' '.join(_fmt_token(w) for w in mk.split())
    mdl_f = ' '.join(_fmt_token(w) for w in mdl.split())
    return ' '.join(p for p in (mk_f, mdl_f) if p)

def _devices_for_calc(calc):
    """Return (devs, makes) for a calc from its assigned devices. plate = device name;
    make = "Make Model" from device metadata (e.g. 'Ford Ranger'), blank if absent."""
    ids = [x['device_id'] for x in _flespi_get(f'/gw/calcs/{calc}/devices/all')]
    devs={}; makes={}
    for i in range(0, len(ids), 150):
        sel = ','.join(str(x) for x in ids[i:i+150])
        for d in _flespi_get(f'/gw/devices/{sel}?fields=id,name,metadata'):
            plate = d.get('name') or str(d['id'])
            md = d.get('metadata') or {}
            devs[d['id']] = plate
            makes[plate] = _fmt_make_model(md.get('make'), md.get('model'))
    return devs, makes

def load_companies():
    companies = [('telemax', TELEMAX_CALC, DEVS, MAKES)]
    try:
        rows = _supa_get('companies?select=slug,flespi_calc_id,is_default')
    except Exception as e:
        print(f'  (companies table unavailable: {e}; scoring Telemax only)')
        rows = []
    for r in rows:
        slug = r.get('slug'); calc = (r.get('flespi_calc_id') or '').strip()
        if slug == 'telemax' or not calc:
            continue
        try:
            devs, makes = _devices_for_calc(calc)
        except Exception as e:
            print(f'  (skip {slug}: could not load calc {calc} devices: {e})'); continue
        if devs:
            companies.append((slug, calc, devs, makes))
    return companies

# ── Run all companies ───────────────────────────────────────────────────────
BASE = os.path.dirname(os.path.abspath(__file__))
scores_all = {}
for slug, calc, devs, makes in load_companies():
    out = score_fleet(calc, devs, makes)
    scores_all[slug] = out
    print(f'[{slug}] {len(out["vehicles"])} vehicles, {out["total_trips"]} trips, '
          f'{len(out["incidents"])} incidents, avg {out["fleet_avg"]} (calc {calc})')

with open(os.path.join(BASE, 'scores_all.json'), 'w') as f:
    json.dump(scores_all, f)
# Back-compat: keep scores_only.json = Telemax for build_html.py / trip_tracks.py
with open(os.path.join(BASE, 'scores_only.json'), 'w') as f:
    json.dump(scores_all.get('telemax', {'vehicles':[],'incidents':[],'fleet_avg':0,'total_trips':0,'num_vehicles':0}), f)
