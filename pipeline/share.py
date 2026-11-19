"""Static event pages and PNG cards for messaging apps that do not read URL fragments.

Only public summaries and report links are used. Cards are cached on the data branch;
the dashboard's normal HTML remains the page, so opening a link still opens the globe.
"""
from __future__ import annotations

import argparse
from functools import lru_cache
import hashlib
import html
import json
import math
import os
from pathlib import Path
import re
import shutil
from urllib.parse import urlparse

from PIL import Image, ImageDraw, ImageFont

import config
import corrections
from extract import EVENT_TYPES
from merge import public_event
from common import load_json, save_json

VERSION = 1
ID = re.compile(r"[a-f0-9]{12}")
STATUS = {'corroborated': 'Corroborated', 'unconfirmed': 'Single source', 'claimed': "One side's claim"}
TYPES = {'deployment': 'Deployment or preparation', 'airstrike': 'Airstrike', 'missile_drone': 'Drone or missile attack',
         'explosion': 'Explosion', 'artillery': 'Shelling', 'ground': 'Ground fighting', 'territory': 'Territorial change',
         'air_defense': 'Air defense', 'naval': 'Naval incident', 'hybrid': 'Hybrid attack', 'incursion': 'Incursion',
         'diplomacy': 'Diplomacy', 'legal': 'Legal step', 'arms_transfer': 'Arms or forces moved', 'production': 'Arms production',
         'weapons_test': 'Weapons test'}


@lru_cache(maxsize=12)
def font(size: int, bold: bool = False):
    names = ['/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf' if bold else '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
             '/System/Library/Fonts/Supplemental/Arial Bold.ttf' if bold else '/System/Library/Fonts/Supplemental/Arial.ttf']
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def wrap(text, face, width):
    lines, line = [], ''
    for word in str(text).split():
        trial = (line + ' ' + word).strip()
        if line and face.getlength(trial) > width:
            lines.append(line)
            line = word
        else:
            line = trial
    if line:
        lines.append(line)
    return lines


def card(event: dict, path: Path) -> None:
    image = Image.new('RGB', (1200, 630), '#0b1017')
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((24, 24, 1176, 606), radius=26, fill='#111b27', outline='#33404e', width=2)
    draw.rectangle((55, 64, 61, 104), fill='#ff5a36')
    draw.text((80, 60), 'GLOBAL SITUATION MONITOR', font=font(27, True), fill='#edf1f7')
    draw.text((80, 103), 'By Huzaifa Khan', font=font(20), fill='#a9b5c4')
    label = 'Warning' if event.get('alert') else TYPES.get(event.get('type'), 'Reported event')
    draw.text((60, 161), label.upper(), font=font(22, True), fill='#ff8a6f')
    # Shrink until every word fits; never turn a tentative report into a shorter factual claim.
    for size in range(44, 23, -1):
        face = font(size, True)
        lines = wrap(event.get('summary') or 'Reported event', face, 1080)
        if len(lines) * (size + 10) <= 232:
            break
    draw.multiline_text((60, 206), '\n'.join(lines), font=face, fill='#f4f6fa', spacing=10)
    place = event.get('place') or event.get('country') or 'Location not named'
    date = str(event.get('time') or '')[:16].replace('T', ' ')
    draw.text((60, 472), f'{place}  ·  {date} UTC', font=font(24), fill='#bec9d7')
    status = 'Possibly an old story' if event.get('possibly_old') else STATUS.get(event.get('status'), 'Unverified')
    draw.text((60, 518), status, font=font(24, True), fill='#e5eaf2')
    draw.text((60, 558), 'Machine-written summary · Open the event for sources', font=font(19), fill='#9baabd')
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format='PNG')


def page(template: str, event: dict, base: str, image_name: str) -> str:
    url = f"{base}events/{event['id']}/"
    image_url = url + image_name
    title = event.get('summary') or 'Reported event'
    label = STATUS.get(event.get('status'), 'Unverified')
    if event.get('possibly_old'):
        label = 'Possibly an old story'
    description = f"{event.get('place') or 'Location not named'} · {label}. Open this event on Global Situation Monitor for its reports and sources."
    esc = html.escape
    metadata = f'''\n  <meta property="og:type" content="article">
  <meta property="og:site_name" content="Global Situation Monitor">
  <meta property="og:title" content="{esc(title, quote=True)}">
  <meta property="og:description" content="{esc(description, quote=True)}">
  <meta property="og:url" content="{esc(url, quote=True)}">
  <meta property="og:image" content="{esc(image_url, quote=True)}">
  <meta property="og:image:type" content="image/png">
  <meta property="og:image:width" content="1200">
  <meta property="og:image:height" content="630">
  <meta property="og:image:alt" content="{esc(title, quote=True)}">
  <meta name="twitter:card" content="summary_large_image">
  <meta name="twitter:title" content="{esc(title, quote=True)}">
  <meta name="twitter:description" content="{esc(description, quote=True)}">
  <meta name="twitter:image" content="{esc(image_url, quote=True)}">
  <link rel="canonical" href="{esc(url, quote=True)}">
  <script id="sharedEvent" type="application/json">{json.dumps(event, ensure_ascii=False).replace('<', chr(92) + 'u003c')}</script>'''
    out = template.replace('<base href="./">', '<base href="../../">', 1)
    out = out.replace('<title>Global Situation Monitor</title>', f'<title>{esc(title)} | Global Situation Monitor</title>', 1)
    out = re.sub(r'<meta name="description" content="[^"]*">',
                 lambda _: f'<meta name="description" content="{esc(description, quote=True)}">', out, count=1)
    return out.replace('</head>', metadata + '\n</head>', 1)


def build(data_dir: Path, archive_dir: Path, out_dir: Path, cache_dir: Path, base_url: str,
          entries=None, removed=None) -> int:
    base = base_url.rstrip('/') + '/'
    parsed = urlparse(base)
    if parsed.scheme not in ('https', 'http') or not parsed.hostname or parsed.query or parsed.fragment:
        raise ValueError('The dashboard URL must be an http(s) site address, without a query or fragment')
    data = load_json(data_dir / 'events.json', {})
    events = {}
    for path in sorted(archive_dir.glob('????-??-??.json')):
        for event in load_json(path, {}).get('events', []):
            if ID.fullmatch(str(event.get('id') or '')):
                events[event['id']] = public_event(event)
    # The corrected current publication wins over the archived copy.
    buckets = {}
    for event in data.get('events', []):
        if not ID.fullmatch(str(event.get('id') or '')):
            continue
        from publish import report_bucket
        b = report_bucket(event['id'])
        if b not in buckets:
            buckets[b] = load_json(data_dir / 'reports' / f'{b:02d}.json', {}).get('reports', {})
        events[event['id']] = {**event, 'reports': buckets[b].get(event['id'], event.get('reports') or [])}
    events = corrections.publish(list(events.values()), entries or [], EVENT_TYPES)
    events = [e for e in events if e['id'] not in (removed or set())]
    root = out_dir / 'events'
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    template = (out_dir / 'index.html').read_text(encoding='utf-8')
    keep = set()
    for event in events:
        visual = {k: event.get(k) for k in ('summary', 'type', 'alert', 'place', 'country', 'time', 'status', 'possibly_old')}
        fingerprint = hashlib.sha256(json.dumps([VERSION, visual], sort_keys=True).encode()).hexdigest()[:16]
        image_name = f'preview-{fingerprint}.png'
        cached = cache_dir / f"{event['id']}-{fingerprint}.png"
        keep.add(cached.name)
        if not cached.exists():
            card(event, cached)
        folder = root / event['id']
        folder.mkdir()
        shutil.copyfile(cached, folder / image_name)
        (folder / 'index.html').write_text(page(template, event, base, image_name), encoding='utf-8')
        save_json(folder / 'event.json', event)
    for path in cache_dir.glob('*.png'):
        if path.name not in keep:
            path.unlink()
    return len(events)


# ---------------------------------------------------------------- tension needles
# The owner, 2026-11-18: "have preview link show the needle". One page per region at
# needles/<region id>/ (the dashboard's own page, opening on that needle), with a drawn card of
# the dial as its preview: the region, its level, how the day compares with its own last 30 days,
# and when the reading was made. Built from the published tension block (pipeline/tension.py).
NEEDLE_BANDS = [(0.0, 0.25, '#2d3137'), (0.25, 0.6, '#464a51'), (0.6, 0.85, '#75787d'), (0.85, 0.97, '#a1645a'), (0.97, 1.0, '#d8483a')]
LEVEL_INK = {'High': '#e0a397', 'Severe': '#e2574a'}
NEEDLE_ID = re.compile(r'[a-z][a-z0-9_]{1,30}')
MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']


def compare_words(pct: int, days: int) -> str:
    """How the day compares with the region's month (the same words as app.js tnCompare)."""
    if pct >= 100:
        return f"Busier than any of the region's last {days} days"
    if pct <= 0:
        return f"Quieter than any of the region's last {days} days"
    if 45 <= pct <= 55:
        return f"About a usual day for the region over its last {days} days"
    if pct > 55:
        return f"Busier than {pct}% of the region's last {days} days"
    return f"Quieter than {100 - pct}% of the region's last {days} days"


def analyst_words(region: dict) -> str:
    """The analyst's nudge, said in words on the card (the dashboard lists the judgments)."""
    net = sum(1 if j.get('trend') == 'escalating' else -1 for j in region.get('analyst') or [])
    if net > 0:
        return " The regional analyst's escalating judgment raised it."
    if net < 0:
        return " The regional analyst's de-escalating judgment lowered it."
    return ''


def needle_lines(region: dict) -> tuple[str, str]:
    """(title, the one-line comparison) in the dashboard's own words (app.js tensionCardHtml)."""
    name = region.get('name') or region['theater']
    level = region.get('level')
    if not level:
        return f'{name}: no tension reading yet', "Not enough of this region's history yet to compare with."
    raw = region.get('raw') if region.get('raw') is not None else region.get('reading', 0.5)
    pct = round(float(raw) * 100)
    n = region.get('days') or 30
    lead = compare_words(pct, n)
    prev, reading = region.get('prev'), region.get('reading')
    if prev is not None and reading is not None and abs(reading - prev) >= 0.1:
        lead += '; up on the day before' if reading > prev else '; down on the day before'
    return f'{name} tension: {level}', lead + '.' + analyst_words(region)


def when(iso) -> str:
    t = str(iso or '')
    if len(t) < 16:
        return ''
    return f"{t[11:16]} UTC, {int(t[8:10])} {MONTHS[int(t[5:7]) - 1]} {t[:4]}"


def needle_card(region: dict, computed_at, path: Path) -> None:
    k = 2  # drawn at twice the size, then reduced, for smooth curves

    def s(v):
        return int(round(v * k))
    image = Image.new('RGB', (1200 * k, 630 * k), '#07090c')
    draw = ImageDraw.Draw(image)
    draw.rectangle((s(56), s(60), s(62), s(98)), fill='#d8483a')
    draw.text((s(80), s(56)), 'GLOBAL SITUATION MONITOR', font=font(s(26), True), fill='#efece6')
    draw.text((s(80), s(96)), 'By Huzaifa Khan', font=font(s(19)), fill='#9a978f')
    draw.text((s(60), s(170)), 'TENSION · LAST 24 HOURS', font=font(s(21), True), fill='#e2574a')
    name = region.get('name') or region['theater']
    level = region.get('level')
    _, lead = needle_lines(region)
    for size in range(50, 30, -2):
        face = font(s(size), True)
        lines = wrap(name, face, s(540))
        if len(lines) <= 2:
            break
    y = 212
    for line in lines:
        draw.text((s(60), s(y)), line, font=face, fill='#f4f1eb')
        y += size + 10
    draw.text((s(60), s(y + 8)), (level or 'No reading yet').upper(), font=font(s(40), True), fill=LEVEL_INK.get(level, '#d9d6cf'))
    lead_face = font(s(23))
    draw.multiline_text((s(60), s(y + 70)), '\n'.join(wrap(lead, lead_face, s(540))), font=lead_face, fill='#c9c6bf', spacing=s(8))
    stamp = when(computed_at)
    if stamp:
        draw.text((s(60), s(510)), f'Reading at {stamp}', font=font(s(20)), fill='#9a978f')
    draw.text((s(60), s(552)), "Judged only against this region's own last 30 days, not other regions.", font=font(s(18)), fill='#807e7a')
    # the dial: a half circle, low on the left, severe on the right
    cx, cy, r, width = s(905), s(420), s(220), s(30)
    box = (cx - r, cy - r, cx + r, cy + r)
    for lo, hi, colour in NEEDLE_BANDS:
        draw.arc(box, 180 + lo * 180 + (0.6 if lo > 0 else 0), 180 + hi * 180 - (0.6 if hi < 1 else 0), fill=colour, width=width)

    def point(x, rad):
        a = math.pi * (1 - min(1.0, max(0.0, float(x))))
        return cx + rad * math.cos(a), cy - rad * math.sin(a)
    if level and region.get('prev') is not None:
        draw.line([point(region['prev'], r + s(6)), point(region['prev'], r + s(26))], fill='#807e7a', width=s(4))
    if level and region.get('reading') is not None:
        draw.line([(cx, cy), point(region['reading'], r - s(18))], fill='#f4f1eb', width=s(6))
    draw.ellipse((cx - s(11), cy - s(11), cx + s(11), cy + s(11)), fill='#f4f1eb')
    small = font(s(16), True)
    for word, x in (('LOW', 0.0), ('SEVERE', 1.0)):
        px, _ = point(x, r)
        draw.text((px - small.getlength(word) / 2, cy + s(22)), word, font=small, fill='#807e7a')
    image = image.resize((1200, 630), Image.LANCZOS)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format='PNG')


def needle_page(template: str, region: dict, base: str, image_name: str) -> str:
    url = f"{base}needles/{region['theater']}/"
    image_url = url + image_name
    title, lead = needle_lines(region)
    description = f"{lead} Each region is judged only against its own past. Open it on Global Situation Monitor to see what moved it."
    esc = html.escape
    tags = [
        ('property', 'og:type', 'article'), ('property', 'og:site_name', 'Global Situation Monitor'),
        ('property', 'og:title', title), ('property', 'og:description', description), ('property', 'og:url', url),
        ('property', 'og:image', image_url), ('property', 'og:image:type', 'image/png'),
        ('property', 'og:image:width', '1200'), ('property', 'og:image:height', '630'),
        ('property', 'og:image:alt', f'{title}. {lead}'), ('name', 'twitter:card', 'summary_large_image'),
        ('name', 'twitter:title', title), ('name', 'twitter:description', description), ('name', 'twitter:image', image_url),
    ]
    metadata = ''.join(f'\n  <meta {kind}="{key}" content="{esc(value, quote=True)}">' for kind, key, value in tags)
    metadata += f'\n  <link rel="canonical" href="{esc(url, quote=True)}">'
    out = template.replace('<base href="./">', '<base href="../../">', 1)
    out = out.replace('<title>Global Situation Monitor</title>', f'<title>{esc(title)} | Global Situation Monitor</title>', 1)
    out = re.sub(r'<meta name="description" content="[^"]*">',
                 lambda _: f'<meta name="description" content="{esc(description, quote=True)}">', out, count=1)
    return out.replace('</head>', metadata + '\n</head>', 1)


def build_needles(data_dir: Path, out_dir: Path, base_url: str) -> int:
    """needles/<region>/index.html and its preview card, for every region in the tension block.
    Rebuilt each run (a handful of small images): the card's file name changes with the reading,
    so apps that keep an old preview image fetch the new one."""
    base = base_url.rstrip('/') + '/'
    tension = load_json(data_dir / 'events.json', {}).get('tension') or {}
    root = out_dir / 'needles'
    if root.exists():
        shutil.rmtree(root)
    regions = [r for r in tension.get('regions') or [] if NEEDLE_ID.fullmatch(str(r.get('theater') or ''))]
    if not regions:
        return 0
    template = (out_dir / 'index.html').read_text(encoding='utf-8')
    for region in regions:
        visual = [VERSION, {k: region.get(k) for k in ('name', 'level', 'reading', 'raw', 'prev', 'days')}, tension.get('computed_at')]
        image_name = 'preview-' + hashlib.sha256(json.dumps(visual, sort_keys=True).encode()).hexdigest()[:16] + '.png'
        folder = root / region['theater']
        needle_card(region, tension.get('computed_at'), folder / image_name)
        (folder / 'index.html').write_text(needle_page(template, region, base, image_name), encoding='utf-8')
    return len(regions)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--base-url')
    args = parser.parse_args()
    base = args.base_url or os.environ.get('DASHBOARD_URL')
    if not base:
        owner, repo = os.environ['GITHUB_REPOSITORY'].split('/', 1)
        base = f'https://{owner}.github.io/{repo}/'
    cfg = config.load()
    count = build(args.data, args.archive, args.out, args.cache, base,
                  corrections.load(config.CONFIG_DIR / 'corrections.yaml'), cfg.removed)
    print(f'[share] {count} event pages built; unchanged preview images reused')
    print(f'[share] {build_needles(args.data, args.out, base)} needle pages built')


if __name__ == '__main__':
    main()
