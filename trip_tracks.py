"""
trip_tracks.py — extract the driven GPS path and the LOCATION of every harsh-driving
event (braking / acceleration / cornering / speeding) for each trip, so the dashboard
can plot *where* events happened.

Event detection replicates the Flespi calc (id 2923614) expressions exactly — same
speed/heading-delta thresholds — but keeps each firing message's lat/lon. Verified to
match the calc's harsh_*_count per trip across all devices.

Output: track_cache.json  {trip_id: {plate, track:[[lat,lon],...], events:[{type,lat,lon,ts,sev},...]}}
Incremental: trips already cached are skipped.

Run standalone:  python trip_tracks.py
"""
import urllib.request, urllib.parse, json, os, math
from dotenv import load_dotenv

BASE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE, '.env'))

TOKEN  = os.environ['FLESPI_TOKEN']
SCORES = os.path.join(BASE, 'scores_only.json')
CACHE  = os.path.join(BASE, 'track_cache.json')

# Same device map as score_v2.py (plate <-> Flespi device id)
DEVS = {6536476:'856KZ4',6605289:'387BX3',6605473:'627KB5',6613713:'136YSI',6711239:'570RSO',
        6884310:'534JDZ',6884322:'934NQ4',6884325:'WHOOP',7419562:'476NP5',7585062:'344IT2',
        7734421:'VolvoXC60',7734429:'873BX8',8180103:'498IO7'}
PLATE_TO_DEV = {pl: did for did, pl in DEVS.items()}

# Calc thresholds (m/s^2 and lateral g) — mirror of calc 2923614 counters
BRAKE_MS2  = -1.8     # harsh_braking_count:  longitudinal accel < -1.8
ACCEL_MS2  = 1.4      # harsh_accel_count:    longitudinal accel >= 1.4
CORNER_G   = 0.3      # harsh_cornering_count: lateral g >= 0.3 (only when speed >= 35 km/h)
CORNER_MIN_SPEED = 35
SPEED_FACTOR = 1.08   # speeding: speed > limit * 1.08
DT_MIN, DT_MAX = 1, 30

# Track decimation: keep a point when it moves >25 m or turns >18 deg from the last kept point
DECIMATE_M   = 25
DECIMATE_DEG = 18


def fetch_messages(dev_id, begin, end):
    params = urllib.parse.urlencode({'data': json.dumps({'from': int(begin), 'to': int(end)})})
    url = f'https://flespi.io/gw/devices/{dev_id}/messages?{params}'
    req = urllib.request.Request(url, headers={'Authorization': f'FlespiToken {TOKEN}'}, method='GET')
    with urllib.request.urlopen(req, timeout=30) as r:
        msgs = json.load(r).get('result', [])
    msgs = [m for m in msgs if m.get('position.valid') and m.get('position.latitude') is not None]
    msgs.sort(key=lambda m: m['timestamp'])
    return msgs


def _haversine_m(a, b):
    R = 6371000
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp = math.radians(b[0] - a[0]); dl = math.radians(b[1] - a[1])
    h = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2 * R * math.asin(min(1, math.sqrt(h)))


def extract(msgs):
    """Return (track, events) replicating the calc's per-message event logic."""
    track = []
    events = []
    last_kept = None
    last_dir = None
    spd_run = None  # active speeding segment: {peak_excess, lat, lon, ts, dur_start}
    prev = None

    for m in msgs:
        lat, lon = m['position.latitude'], m['position.longitude']
        ts = m['timestamp']
        speed = m.get('position.speed', 0) or 0
        direction = m.get('position.direction')

        # ---- decimated track ----
        keep = last_kept is None or _haversine_m(last_kept, (lat, lon)) >= DECIMATE_M
        if not keep and direction is not None and last_dir is not None:
            dd = abs(direction - last_dir); dd = min(dd, 360 - dd)
            keep = dd >= DECIMATE_DEG
        if keep:
            track.append([round(lat, 6), round(lon, 6)])
            last_kept = (lat, lon); last_dir = direction

        # ---- per-message events (need a usable previous message) ----
        if prev is not None:
            dt = ts - prev['timestamp']
            if DT_MIN <= dt < DT_MAX:
                a = ((speed - (prev.get('position.speed', 0) or 0)) / 3.6) / dt  # m/s^2
                if a < BRAKE_MS2:
                    events.append({'type': 'brk', 'lat': round(lat,6), 'lon': round(lon,6),
                                   'ts': int(ts), 'sev': round(-a, 2)})
                if a >= ACCEL_MS2:
                    events.append({'type': 'acc', 'lat': round(lat,6), 'lon': round(lon,6),
                                   'ts': int(ts), 'sev': round(a, 2)})
                pd = prev.get('position.direction')
                if speed >= CORNER_MIN_SPEED and direction is not None and pd is not None:
                    dd = abs(direction - pd); dd = min(dd, 360 - dd)
                    lat_g = (speed / 3.6) * (dd / dt * math.pi / 180) / 9.81
                    if lat_g >= CORNER_G:
                        events.append({'type': 'crn', 'lat': round(lat,6), 'lon': round(lon,6),
                                       'ts': int(ts), 'sev': round(lat_g, 2)})

        # ---- speeding (segment-based: one marker at the peak of each run) ----
        lim = m.get('wialon.speed.limit')
        hdop_ok = m.get('position.hdop') is None or m.get('position.hdop') <= 2.0
        is_speeding = bool(lim) and hdop_ok and speed > lim * SPEED_FACTOR
        if is_speeding:
            excess = speed - lim
            if spd_run is None or excess > spd_run['sev']:
                if spd_run is None:
                    spd_run = {'type': 'spd', 'lat': round(lat,6), 'lon': round(lon,6),
                               'ts': int(ts), 'sev': round(excess)}
                else:
                    spd_run.update({'lat': round(lat,6), 'lon': round(lon,6),
                                    'ts': int(ts), 'sev': round(excess)})
        else:
            if spd_run is not None:
                events.append(spd_run); spd_run = None

        prev = m

    if spd_run is not None:
        events.append(spd_run)

    # ensure trip endpoints are in the track
    if msgs and not track:
        track = [[round(msgs[0]['position.latitude'],6), round(msgs[0]['position.longitude'],6)]]
    return track, events


def build(scores_path=SCORES, cache_path=CACHE):
    d = json.load(open(scores_path))
    cache = json.load(open(cache_path)) if os.path.exists(cache_path) else {}

    todo = []
    for v in d['vehicles']:
        dev = PLATE_TO_DEV.get(v['plate'])
        if not dev:
            continue
        for t in v['trips']:
            tid = str(t['id'])
            if tid in cache:
                continue
            if t.get('begin_ts') and t.get('end_ts'):
                todo.append((tid, dev, v['plate'], t['begin_ts'], t['end_ts']))

    print(f'trip_tracks: {len(todo)} new trips to process ({len(cache)} cached)')
    for tid, dev, plate, begin, end in todo:
        try:
            msgs = fetch_messages(dev, begin, end)
            track, events = extract(msgs)
            cache[tid] = {'plate': plate, 'track': track, 'events': events}
            ne = {'brk':0,'acc':0,'crn':0,'spd':0}
            for e in events: ne[e['type']] += 1
            print(f'  #{tid} {plate}: {len(track)} pts, events brk/acc/crn/spd={ne["brk"]}/{ne["acc"]}/{ne["crn"]}/{ne["spd"]}')
        except Exception as e:
            print(f'  #{tid} {plate}: ERROR {e}')

    json.dump(cache, open(cache_path, 'w'))
    print(f'trip_tracks: cache now has {len(cache)} trips -> {cache_path}')
    return cache


if __name__ == '__main__':
    build()
