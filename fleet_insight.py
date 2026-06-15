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

# Trip-size buckets (km) — match the dashboard's tripSize() and supabase_sync.
SHORT_MAX = 10   # < 10 km            -> short
LONG_MIN  = 25   # >= 25 km           -> long   (standard = 10 .. <25)

# Risk bands on the fleet average. Risk is a pure function of the fleet average so the
# bracket is meaningful and trackable — the panel shows these edges so a manager can see
# how far they are from the next band. (Incident detail is surfaced in the stars + summary.)
RISK_LOW_MIN  = 90.0   # >= 90.0 -> Low (aligns with the dashboard's "Excellent" / 4-star tier)
RISK_HIGH_MAX = 70.0   # <  70.0 -> High ;  70.0 .. <90.0 -> Moderate


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


def _fleet_avg_1dp(vehicles):
    """Mean of per-vehicle km-weighted trip totals, to 1 dp — identical to the value the
    dashboard headline shows (supabase_sync.fleet_avg_1dp), so the panel matches it."""
    def precise(v):
        trips = v.get('trips', [])
        km = sum(t.get('km', 0) for t in trips) or 1
        return sum(t.get('total', t.get('raw', 0)) * t.get('km', 0) for t in trips) / km
    avgs = [precise(v) for v in vehicles if v.get('trips')]
    return round(sum(avgs) / len(avgs), 1) if avgs else 0.0


def _trip_mix(vehicles):
    """Fleet-wide count of short / standard / long trips."""
    trips = [t for v in vehicles for t in v.get('trips', [])]
    short = sum(1 for t in trips if t.get('km', 0) < SHORT_MAX)
    lng = sum(1 for t in trips if t.get('km', 0) >= LONG_MIN)
    return {'short': short, 'standard': len(trips) - short - lng, 'long': lng}


def _risk(fleet_avg):
    """Low / Moderate / High as a pure function of the fleet average (see RISK_* bands)."""
    if fleet_avg < RISK_HIGH_MAX:
        return 'High', f'fleet average {fleet_avg:.1f} is below {RISK_HIGH_MAX:.1f}'
    if fleet_avg < RISK_LOW_MIN:
        return 'Moderate', f'fleet average {fleet_avg:.1f} is between {RISK_HIGH_MAX:.1f} and {RISK_LOW_MIN:.1f}'
    return 'Low', f'fleet average {fleet_avg:.1f} is at or above {RISK_LOW_MIN:.1f}'


def _worst_component(comps):
    valid = {k: v for k, v in comps.items() if v is not None}
    return min(valid, key=valid.get) if valid else 'crn'


def _trend_phrase(fleet_avg, prev_avg):
    """Qualitative trend (no number — the prose must not cite a figure that can drift)."""
    if prev_avg is None:
        return 'no prior week to compare against'
    delta = fleet_avg - prev_avg
    if delta >= 2:
        return 'notably higher than last week'
    if delta >= 0.5:
        return 'slightly higher than last week'
    if delta <= -2:
        return 'notably lower than last week'
    if delta <= -0.5:
        return 'slightly lower than last week'
    return 'about the same as last week'


def _fallback_summary(stats):
    """Deterministic template summary (used when the AI call is unavailable).

    Qualitative on purpose — no fleet-average number and no risk word, because the
    live badge/headline own those (the prose is a weekly snapshot and must not drift
    out of agreement with them)."""
    fa = stats['fleet_avg']; nv = stats['num_vehicles']; tt = stats['total_trips']
    worst = WNAMES.get(stats['worst'], 'cornering'); n_inc = stats['n_incident_vehicles']
    if fa >= 90:
        s1 = (f"The fleet is performing strongly across {nv} vehicles and {tt} trips, "
              f"with {worst} the only area showing meaningful room to improve.")
    elif fa >= 70:
        s1 = (f"The fleet is driving well overall across {nv} vehicles and {tt} trips, "
              f"with {worst} the main area to work on.")
    else:
        s1 = (f"The fleet needs attention across {nv} vehicles and {tt} trips, with "
              f"{worst} the primary issue pulling performance down.")
    if n_inc:
        s2 = (f" {n_inc} of {nv} vehicles recorded confirmed speeding incidents this "
              f"period, so {worst} should be the focus.")
    else:
        s2 = " No confirmed speeding incidents this period."
    return (s1 + s2).strip()


def _claude_text(prompt, max_tokens=250):
    """One Claude Messages API call (raw HTTPS, no SDK). Returns the text, or None on
    no-key / error / empty so callers can fall back deterministically."""
    key = os.environ.get('ANTHROPIC_API_KEY')
    if not key:
        return None
    body = json.dumps({
        'model': MODEL, 'max_tokens': max_tokens,
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
        return text or None
    except urllib.error.HTTPError as e:
        print(f'  fleet_insight: Claude HTTP {e.code}: {e.read().decode()[:200]}')
    except Exception as e:
        print(f'  fleet_insight: Claude call failed ({e})')
    return None


def _ai_summary(stats):
    """Fleet 1-2 sentence summary via Claude (qualitative). Returns (text, ai_used)."""
    comps = stats['comps']; mix = stats['mix']

    def c1(v):
        return 'n/a' if v is None else f'{v:.1f}'

    prompt = (
        "You are writing a brief fleet-safety summary for a vehicle-fleet dashboard.\n\n"
        "Context for the current reporting period (for your judgement only — do NOT quote "
        "these figures verbatim):\n"
        f"- Fleet average safety score: {stats['fleet_avg']:.1f}/100 across "
        f"{stats['num_vehicles']} vehicles and {stats['total_trips']} trips.\n"
        f"- Component scores (0-100, higher is safer): Speeding {c1(comps['spd'])}, "
        f"Braking {c1(comps['brk'])}, Acceleration {c1(comps['acc'])}, Cornering {c1(comps['crn'])}.\n"
        f"- Trip mix: {mix['short']} short (<10 km), {mix['standard']} standard (10-25 km), "
        f"{mix['long']} long (>=25 km).\n"
        f"- Weakest area: {WNAMES.get(stats['worst'], 'cornering')}.\n"
        f"- Vehicles with confirmed speeding incidents this period: {stats['n_incident_vehicles']}.\n"
        f"- Trend vs last week: {stats['trend_phrase']}.\n\n"
        "Write a 1-2 sentence plain-English summary for a fleet manager covering how the "
        "fleet is performing overall, the trend, and the single most important issue to "
        "watch.\n"
        "IMPORTANT: Do NOT state the fleet-average score and do NOT name a risk level "
        "(Low / Moderate / High) — both are displayed live beside your summary and would "
        "contradict it as the data updates. Describe performance qualitatively instead "
        "(e.g. 'performing strongly', 'driving well', 'needs attention'). You MAY state the "
        "count of vehicles with confirmed incidents. Do not invent any figures. No markdown, "
        "no bullet points, no preamble; reply with just the summary sentences."
    )
    text = _claude_text(prompt, max_tokens=250)
    return (text, True) if text else (_fallback_summary(stats), False)


def _vehicle_stats(v):
    """Per-vehicle stats computed from trips (robust — works on the lean CI scores too)."""
    comps = _fleet_components([v])  # km-weighted per-component over this vehicle's trips
    trips = v.get('trips', [])
    return {
        'make': v.get('make', 'vehicle'),
        'avg': v.get('avg', 0),
        'trips': len(trips),
        'comps': comps,
        'worst': _worst_component(comps),
        'n_inc': sum(1 for t in trips if t.get('incident')),
    }


def _vehicle_fallback_summary(vs):
    """Deterministic per-vehicle summary (no score number — the card shows it live)."""
    make = vs['make']; worst = WNAMES.get(vs['worst'], 'cornering'); avg = vs['avg']; n_inc = vs['n_inc']
    if avg >= 90:
        s1 = "This vehicle is delivering outstanding driving behaviour with strong performance across the board."
    elif avg >= 70:
        s1 = f"The {make} is driving well overall, with {worst} the main area with room to improve."
    elif avg >= 50:
        s1 = f"The {make} has clear room to improve, with {worst} the primary issue holding the score back."
    else:
        s1 = f"The {make} needs significant attention, with {worst} consistently the main concern to address."
    s2 = (f" {vs['n_inc']} confirmed speeding incident{'s' if n_inc != 1 else ''} recorded this period."
          if n_inc else " No confirmed speeding incidents this period.")
    return (s1 + s2).strip()


def _vehicle_ai_summary(vs):
    """Per-vehicle 1-2 sentence summary via Claude (qualitative). Returns (text, ai_used)."""
    c = vs['comps']

    def c1(x):
        return 'n/a' if x is None else f'{x:.1f}'

    prompt = (
        "You are writing a short driving summary for ONE vehicle on a fleet-safety dashboard.\n\n"
        "Context (for your judgement only — do NOT quote these figures verbatim):\n"
        f"- Vehicle: {vs['make']}, {vs['trips']} trips this period.\n"
        f"- Overall safety score: {vs['avg']}/100.\n"
        f"- Component scores (0-100, higher is safer): Speeding {c1(c['spd'])}, "
        f"Braking {c1(c['brk'])}, Acceleration {c1(c['acc'])}, Cornering {c1(c['crn'])}.\n"
        f"- Weakest area: {WNAMES.get(vs['worst'], 'cornering')}.\n"
        f"- Confirmed speeding incidents this period: {vs['n_inc']}.\n\n"
        "Write 1-2 short sentences for a fleet manager about THIS vehicle: how it is driving "
        "overall and the single main thing to improve (or, if it is excellent, affirm the "
        "strong performance).\n"
        "IMPORTANT: Do NOT state the score number, a star rating, a fleet rank, or a trend "
        "direction — those are all shown live next to your summary and would contradict it as "
        "the data updates. Describe performance qualitatively (e.g. 'driving well', 'room to "
        "improve'). You MAY mention the count of confirmed incidents. Do not invent figures. "
        "No markdown, no preamble; reply with just the sentence(s)."
    )
    text = _claude_text(prompt, max_tokens=200)
    return (text, True) if text else (_vehicle_fallback_summary(vs), False)


def build(scores_path=SCORES, cache_path=CACHE, force=False):
    week_of = _current_monday()
    cache = json.load(open(cache_path)) if os.path.exists(cache_path) else None

    # Weekly gate: reuse unless it's a new week (or no usable cache yet).
    if (cache and cache.get('week_of') == week_of and cache.get('summary')
            and cache.get('vehicles') and not force):
        print(f'  fleet_insight: cache current for week of {week_of} — reusing (no Claude call)')
        return cache

    d = json.load(open(scores_path))
    vehicles = d.get('vehicles', [])
    comps = _fleet_components(vehicles)
    fleet_avg = _fleet_avg_1dp(vehicles)  # 1 dp, matches the dashboard headline
    mix = _trip_mix(vehicles)
    n_inc = _incident_vehicles(vehicles)
    num_vehicles = d.get('num_vehicles', len(vehicles))
    risk, risk_reason = _risk(fleet_avg)
    worst = _worst_component(comps)
    prev_avg = cache.get('fleet_avg') if cache else None  # last week's, for the trend

    stats = {
        'fleet_avg': fleet_avg, 'num_vehicles': num_vehicles,
        'total_trips': d.get('total_trips', 0), 'comps': comps, 'mix': mix, 'worst': worst,
        'n_incident_vehicles': n_inc, 'risk': risk,
        'trend_phrase': _trend_phrase(fleet_avg, prev_avg),
    }
    summary, ai = _ai_summary(stats)

    # Per-vehicle summaries (one weekly Claude call each, keyed by plate; qualitative so
    # they never contradict the vehicle card's live score / stars / rank / trend).
    veh_summaries = {}
    veh_ai = 0
    for v in vehicles:
        vs = _vehicle_stats(v)
        v_text, v_used = _vehicle_ai_summary(vs)
        veh_summaries[v['plate']] = {'summary': v_text, 'ai': v_used}
        veh_ai += 1 if v_used else 0

    out = {
        'week_of': week_of,
        'generated_at': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'fleet_avg': fleet_avg, 'prev_avg': prev_avg,
        'total_trips': stats['total_trips'], 'num_vehicles': stats['num_vehicles'],
        'spd': comps['spd'], 'brk': comps['brk'], 'acc': comps['acc'], 'crn': comps['crn'],
        'short': mix['short'], 'standard': mix['standard'], 'long': mix['long'],
        'n_incident_vehicles': n_inc, 'risk': risk, 'risk_reason': risk_reason,
        'risk_low_min': RISK_LOW_MIN, 'risk_high_max': RISK_HIGH_MAX,
        'summary': summary, 'ai': ai,
        'vehicles': veh_summaries,
    }
    json.dump(out, open(cache_path, 'w'))
    print(f'  fleet_insight: generated for week of {week_of} '
          f'(fleet risk {risk}, fleet ai={ai}, {veh_ai}/{len(vehicles)} vehicle summaries AI) -> {cache_path}')
    print(f'    {summary}')
    return out


if __name__ == '__main__':
    import sys
    build(force='--force' in sys.argv)
