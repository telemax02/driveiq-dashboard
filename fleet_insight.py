"""
fleet_insight.py — weekly fleet-wide insight panel (dashboard "Fleet breakdown").

Produces the data for the panel next to the Fleet-average card:
  1. fleet-wide component STAR scores  — spd / brk / acc / crn (0-100, km-weighted
     across every trip in the fleet) so a manager sees at a glance where the whole
     fleet needs to improve.
  2. a current RISK LEVEL               — Low / Moderate / High, auto-derived from the
     fleet average + confirmed speeding incidents.
  3. a 1-2 sentence AI-written SUMMARY  — Claude Messages API (raw HTTPS, no SDK, to
     match score_v2 / supabase_sync). Plain-language read on overall performance, the
     week-over-week trend, and the single issue worth watching. If ANTHROPIC_API_KEY
     is absent or the call fails, a deterministic template summary is used instead so
     the panel never goes blank.

CACHED WEEKLY — the whole block regenerates only on the first run on/after **Monday
00:00 (AEST)**. Every other run reuses fleet_insight_cache.json untouched. Because CI
checks out a fresh tree each run, the cache file MUST be committed (see update.yml) or
the weekly gate resets and Claude would be called every 4 h instead of once a week.

Output: fleet_insight_cache.json
  {week_of, generated_at, fleet_avg, prev_avg, total_trips, num_vehicles,
   spd, brk, acc, crn, n_incident_vehicles, risk, risk_reason, summary, ai}

Run standalone:  python fleet_insight.py
"""
import json, os, datetime, urllib.request, urllib.error
from dotenv import load_dotenv

BASE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE, '.env'))

SCORES = os.path.join(BASE, 'scores_only.json')
CACHE  = os.path.join(BASE, 'fleet_insight_cache.json')

BNE = 36000  # AEST (Brisbane, no DST) offset in seconds — same as the rest of the pipeline
MODEL = 'claude-opus-4-8'
WNAMES = {'spd': 'speeding', 'brk': 'harsh braking', 'acc': 'harsh acceleration', 'crn': 'cornering'}
LABELS = {'spd': 'Speeding', 'brk': 'Braking', 'acc': 'Acceleration', 'crn': 'Cornering'}


def _current_monday():
    """ISO date (YYYY-MM-DD) of the most recent Monday 00:00 in AEST."""
    aest_now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) \
        + datetime.timedelta(seconds=BNE)
    mon = (aest_now - datetime.timedelta(days=aest_now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0)
    return mon.strftime('%Y-%m-%d')


def _fleet_components(vehicles):
    """km-weighted mean of each component across every trip (spd skips no-coverage trips)."""
    trips = [t for v in vehicles for t in v.get('trips', [])]

    def kmw(key, skip_none=False):
        ts = [t for t in trips if t.get(key) is not None] if skip_none else trips
        km = sum(t.get('km', 0) for t in ts)
        if not km:
            return None
        return round(sum((t.get(key) or 0) * t.get('km', 0) for t in ts) / km)

    return {'spd': kmw('spd', skip_none=True), 'brk': kmw('brk'),
            'acc': kmw('acc'), 'crn': kmw('crn')}


def _incident_vehicles(vehicles):
    return sum(1 for v in vehicles if any(t.get('incident') for t in v.get('trips', [])))


def _risk(fleet_avg, n_inc, num_vehicles):
    """Low / Moderate / High from the fleet average + confirmed speeding incidents.

    The average is the dominant signal of overall safety; the confirmed-incident
    share escalates it. High is reserved for a genuinely weak average or near-universal
    incidents — a strong average with some speeding lands on Moderate, not High."""
    rate = n_inc / max(1, num_vehicles)
    if fleet_avg < 75 or rate >= 0.8:
        return 'High', f'fleet average {fleet_avg} with {n_inc}/{num_vehicles} vehicles flagged for incidents'
    if fleet_avg < 87 or n_inc >= 2:
        return 'Moderate', f'fleet average {fleet_avg} with {n_inc} confirmed incident vehicle(s)'
    return 'Low', f'strong fleet average {fleet_avg} and minimal confirmed incidents'


def _worst_component(comps):
    valid = {k: v for k, v in comps.items() if v is not None}
    return min(valid, key=valid.get) if valid else 'crn'


def _trend_phrase(fleet_avg, prev_avg):
    if prev_avg is None:
        return 'no prior week to compare against'
    delta = round(fleet_avg - prev_avg, 1)
    if delta >= 0.5:
        return f'up {delta} points'
    if delta <= -0.5:
        return f'down {abs(delta)} points'
    return 'effectively unchanged'


def _fallback_summary(stats):
    """Deterministic template summary (used when the AI call is unavailable)."""
    fa = stats['fleet_avg']; nv = stats['num_vehicles']; tt = stats['total_trips']
    worst = WNAMES.get(stats['worst'], 'cornering'); n_inc = stats['n_incident_vehicles']
    if fa >= 90:
        s1 = (f"The fleet is performing strongly, averaging {fa} across {nv} vehicles "
              f"and {tt} trips, with {worst} the only area showing meaningful room to improve.")
    elif fa >= 70:
        s1 = (f"The fleet is driving well overall, averaging {fa} across {nv} vehicles "
              f"and {tt} trips, with {worst} the main area to work on.")
    else:
        s1 = (f"The fleet needs attention, averaging {fa} across {nv} vehicles and "
              f"{tt} trips, with {worst} the primary issue pulling scores down.")
    if n_inc:
        s2 = (f" {n_inc} vehicle{'s' if n_inc > 1 else ''} recorded confirmed speeding "
              f"incidents this period — the fleet is at {stats['risk'].lower()} risk and "
              f"{worst} should be the focus.")
    else:
        s2 = (f" No confirmed speeding incidents this period; the fleet is at "
              f"{stats['risk'].lower()} risk.")
    return (s1 + s2).strip()


def _ai_summary(stats):
    """1-2 sentence summary via the Claude Messages API (raw HTTPS). Returns (text, ok)."""
    key = os.environ.get('ANTHROPIC_API_KEY')
    if not key:
        return _fallback_summary(stats), False

    comps = stats['comps']
    prompt = (
        "You are writing a brief fleet-safety summary for a vehicle-fleet dashboard.\n\n"
        "Data for the current reporting period:\n"
        f"- Fleet average safety score: {stats['fleet_avg']}/100 across "
        f"{stats['num_vehicles']} vehicles and {stats['total_trips']} trips.\n"
        f"- Component scores (0-100, higher is safer): Speeding {comps['spd']}, "
        f"Braking {comps['brk']}, Acceleration {comps['acc']}, Cornering {comps['crn']}.\n"
        f"- Weakest area: {WNAMES.get(stats['worst'], 'cornering')}.\n"
        f"- Vehicles with confirmed speeding incidents this period: {stats['n_incident_vehicles']}.\n"
        f"- Change vs last week's fleet average: {stats['trend_phrase']}.\n"
        f"- Current risk level: {stats['risk']}.\n\n"
        "Write a 1-2 sentence plain-English summary for a fleet manager covering how the "
        "fleet is performing overall, the trend, and the single most important issue to "
        "watch. Use only the numbers above — do not invent any figures. No markdown, no "
        "bullet points, no preamble; reply with just the summary sentences."
    )
    body = json.dumps({
        'model': MODEL,
        'max_tokens': 250,
        'messages': [{'role': 'user', 'content': prompt}],
    }).encode()
    req = urllib.request.Request(
        'https://api.anthropic.com/v1/messages', data=body, method='POST',
        headers={'x-api-key': key, 'anthropic-version': '2023-06-01',
                 'content-type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            data = json.load(r)
        text = ''.join(b.get('text', '') for b in data.get('content', [])
                       if b.get('type') == 'text').strip()
        if text:
            return text, True
        print('  fleet_insight: empty AI response, using fallback')
    except urllib.error.HTTPError as e:
        print(f'  fleet_insight: Claude HTTP {e.code}: {e.read().decode()[:200]} — using fallback')
    except Exception as e:
        print(f'  fleet_insight: Claude call failed ({e}) — using fallback')
    return _fallback_summary(stats), False


def build(scores_path=SCORES, cache_path=CACHE, force=False):
    week_of = _current_monday()
    cache = json.load(open(cache_path)) if os.path.exists(cache_path) else None

    # Weekly gate: reuse unless it's a new week (or no usable cache yet).
    if cache and cache.get('week_of') == week_of and cache.get('summary') and not force:
        print(f'  fleet_insight: cache current for week of {week_of} — reusing (no Claude call)')
        return cache

    d = json.load(open(scores_path))
    vehicles = d.get('vehicles', [])
    comps = _fleet_components(vehicles)
    fleet_avg = d.get('fleet_avg', 0)
    n_inc = _incident_vehicles(vehicles)
    num_vehicles = d.get('num_vehicles', len(vehicles))
    risk, risk_reason = _risk(fleet_avg, n_inc, num_vehicles)
    worst = _worst_component(comps)
    prev_avg = cache.get('fleet_avg') if cache else None  # last week's, for the trend

    stats = {
        'fleet_avg': fleet_avg, 'num_vehicles': num_vehicles,
        'total_trips': d.get('total_trips', 0), 'comps': comps, 'worst': worst,
        'n_incident_vehicles': n_inc, 'risk': risk,
        'trend_phrase': _trend_phrase(fleet_avg, prev_avg),
    }
    summary, ai = _ai_summary(stats)

    out = {
        'week_of': week_of,
        'generated_at': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'fleet_avg': fleet_avg, 'prev_avg': prev_avg,
        'total_trips': stats['total_trips'], 'num_vehicles': stats['num_vehicles'],
        'spd': comps['spd'], 'brk': comps['brk'], 'acc': comps['acc'], 'crn': comps['crn'],
        'n_incident_vehicles': n_inc, 'risk': risk, 'risk_reason': risk_reason,
        'summary': summary, 'ai': ai,
    }
    json.dump(out, open(cache_path, 'w'))
    print(f'  fleet_insight: generated for week of {week_of} '
          f'(risk {risk}, ai={ai}) -> {cache_path}')
    print(f'    {summary}')
    return out


if __name__ == '__main__':
    import sys
    build(force='--force' in sys.argv)
