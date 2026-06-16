"""
build_html.py

Two jobs:
  1. (pipeline) Reverse-geocode each trip's start/end coords into from/to street
     addresses and write them back into scores_only.json, so supabase_sync.py
     picks them up. Skipped automatically when scores_only.json isn't present
     (e.g. a local HTML-only rebuild).
  2. Build docs/index.html from combined_template.html by injecting the Supabase
     publishable config. All dashboard DATA loads at runtime from Supabase, so the
     only build-time tokens are __SUPA_URL__ / __SUPA_KEY__.

Static assets (docs/styles.css, docs/app.js, docs/favicon.svg) are committed and
served as-is — they are NOT generated here.
"""
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

# ── 1. Geocoding pass (only when the scores file exists) ────────────────────
if os.path.exists(SCORES):
    import urllib.request as _ur

    def _rev(lat, lon):
        try:
            url = f'https://photon.komoot.io/reverse?lat={lat}&lon={lon}'
            req = _ur.Request(url, headers={'User-Agent': 'Telemax-VSS/1.0'})
            with _ur.urlopen(req, timeout=7) as r:
                p = json.load(r).get('features', [{}])[0].get('properties', {})
            road = p.get('street') or p.get('name') or ''
            sub  = p.get('district') or p.get('suburb') or p.get('city') or ''
            return ', '.join(x for x in [road, sub] if x)
        except Exception:
            return ''

    d = json.load(open(SCORES))
    cache = json.load(open(GEO)) if os.path.exists(GEO) else {}

    new_coords = []
    for v in d['vehicles']:
        for t in v['trips']:
            for field, lk, lok in [('from', 'slat', 'slon'), ('to', 'elat', 'elon')]:
                if t.get(lk) and t.get(lok):
                    key = f'{round(t[lk],3)},{round(t[lok],3)}'
                    if key not in cache or not cache[key]:
                        new_coords.append(key)

    for key in set(new_coords):
        lat, lon = key.split(',')
        cache[key] = _rev(float(lat), float(lon))
        print(f'  Geocoded {key} -> {cache[key]}')

    if new_coords:
        json.dump(cache, open(GEO, 'w'))

    for v in d['vehicles']:
        for t in v['trips']:
            for field, lk, lok in [('from', 'slat', 'slon'), ('to', 'elat', 'elon')]:
                if not t.get(field) and t.get(lk) and t.get(lok):
                    key = f'{round(t[lk],3)},{round(t[lok],3)}'
                    t[field] = cache.get(key, '')

    # Write geocoded addresses back so supabase_sync.py picks them up
    json.dump(d, open(SCORES, 'w'))
else:
    print('build_html: scores_only.json not found - skipping geocoding (HTML-only build)')

# ── 2. Build docs/index.html (inject Supabase config only) ──────────────────
template = open(os.path.join(BASE, 'combined_template.html'), encoding='utf-8').read()
html = (template
        .replace('__SUPA_URL__', os.environ.get('SUPABASE_URL', ''))
        .replace('__SUPA_KEY__', os.environ.get('SUPABASE_KEY', '')))

os.makedirs(os.path.join(BASE, 'docs'), exist_ok=True)
out = os.path.join(BASE, 'docs', 'index.html')
with open(out, 'w', encoding='utf-8') as f:
    f.write(html)
print(f'Built: {out}')
