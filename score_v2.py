import urllib.request, json, datetime, os
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))

TOKEN = os.environ['FLESPI_TOKEN']
CALC  = os.environ.get('FLESPI_CALC_ID', '2923614')
BNE=36000
# Data window starts 2 Jun 2026 00:00 AEST
TODAY = int((datetime.datetime(2026, 6, 2) - datetime.datetime(1970, 1, 1)).total_seconds()) - BNE
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
        spd=sub(sx/cv,0,1.0); raw=round(spd_w*spd+brk_w*brk+crn_w*crn+acc_w*acc)
    else:
        spd=None; raw=round(brk_w*brk+crn_w*crn+acc_w*acc)
    shrink=min(1.0,km/20); total=round(shrink*raw+(1-shrink)*fleet_mean)
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

def fetch(d):
    req=urllib.request.Request(f'https://flespi.io/gw/calcs/{CALC}/devices/{d}/intervals/all',
        data=json.dumps({'count':200,'reverse':True}).encode(),
        headers={'Authorization':f'FlespiToken {TOKEN}','Content-Type':'application/json'},method='GET')
    with urllib.request.urlopen(req,timeout=12) as r: return json.load(r).get('result',[])

raw_trips=[]; raw_intervals={}
for did,pl in DEVS.items():
    ints=[t for t in fetch(did) if t.get('begin',0)>=TODAY and t.get('mileage_km',0)>=3.0 and t.get('moving_time_s',0)>=240]
    raw_intervals[did]=(pl,ints)
    for t in ints:
        m=t.get('moving_time_s',1) or 1; dur=t.get('duration',m)
        if dur<=m*5: raw_trips.append(t.get('mileage_km',0))

FLEET_MEAN=80
veh=[]; inc=[]
for did,(pl,ints) in raw_intervals.items():
    trips=[score_trip(t,FLEET_MEAN) for t in ints]
    trips=[t for t in trips if t is not None]
    if not trips: continue
    km_total=sum(t['km'] for t in trips)
    avg=round(sum(t['total']*t['km'] for t in trips)/km_total) if km_total>0 else 0
    cov_trips=[t for t in trips if t['cov_pct']>0]
    avg_cov=round(sum(t['cov_pct'] for t in cov_trips)/len(cov_trips)) if cov_trips else 0
    low_cov=avg_cov<60 and len(cov_trips)>0
    veh.append({'plate':pl,'make':MAKES[pl],'avg':avg,'inc':any(t['incident'] for t in trips),
                'avg_cov':avg_cov,'low_cov':low_cov,'trips':trips})
    [inc.append({'plate':pl,'make':MAKES[pl],'trip':f'#{t["id"]}','time':t['t'],'date':t['date'],
                 'mx':t['inc_mx'],'dur':t['inc_dur'],'avg':t['inc_avg'],
                 'begin_ts':t['begin_ts'],'end_ts':t['end_ts'],'dev_id':did,
                 'datetime':'','speed':'','loc':'','coords':[]})
     for t in trips if t['incident']]

veh.sort(key=lambda x:x['avg'],reverse=True); inc.sort(key=lambda x:x['mx'],reverse=True)
fa=round(sum(v['avg'] for v in veh)/len(veh)) if veh else 0
out={'vehicles':veh,'incidents':inc,'fleet_avg':fa,'total_trips':sum(len(v['trips']) for v in veh),
     'generated':datetime.datetime.utcnow().strftime('%d %b %Y %H:%M UTC'),'num_vehicles':13}
import os as _os
_out_path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), 'scores_only.json')
with open(_out_path,'w') as f: json.dump(out,f)
print(f'{len(veh)} vehicles, {out["total_trips"]} trips, {len(inc)} incidents, avg {fa}')
for v in veh:
    print(f'  {v["plate"]:10} {v["avg"]:3} {"INC" if v["inc"] else "   "} {len(v["trips"])}t  cov:{v["avg_cov"]}%{"LOW" if v["low_cov"] else ""}')
