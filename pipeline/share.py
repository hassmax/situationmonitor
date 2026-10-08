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
         'diplomacy': 'Diplomacy', 'legal': 'Legal step', 'arms_transfer': 'Arms or forces moved', 'production': 'Arms production'}


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


if __name__ == '__main__':
    main()
