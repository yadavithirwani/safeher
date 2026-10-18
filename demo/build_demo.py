"""Build the current demo from verified live capture and natural narration.
"""
from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import final_edit as editor

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'demo/out/updated'
CAPTURE = OUT / 'capture-20261018-150520'
STORY = ROOT / 'demo/final_story.json'
MEDIA = ROOT / 'docs/media'
FONT = editor.FONT
BOLD = editor.BOLD
SIZE = (1920, 1080)


def font(size: int, bold: bool = False):
    return ImageFont.truetype(BOLD if bold else FONT, size)


def text(draw, position, value, size=32, color='white', width=1650, bold=False):
    face = font(size, bold)
    for i, line in enumerate(editor.wrap_pixels(draw, value, face, width)):
        draw.text((position[0], position[1] + i * (size + 14)), line, font=face, fill=color)


def card(scene, path, is_card=False):
    if not is_card:
        original_overlay(scene, path, False)
        return
    image = Image.new('RGBA', SIZE, '#110d1f')
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((170, 140, 1730, 320), radius=24, fill='#292139')
    text(draw, (215, 175), 'Amma' if scene['key'] == 'opening' else 'Meera', 30, '#c4b5fd')
    text(draw, (215, 230), 'Reached?' if scene['key'] == 'opening' else 'Reached.', 48, bold=True)
    if scene['key'] == 'opening':
        text(draw, (180, 425), '1,195 reported cases in 2023', 66, bold=True)
        text(draw, (180, 525), 'Compared with 592 in 2021 · Bengaluru city-wide context', 34, '#ddd6fe')
        text(draw, (180, 660), 'Source: Bengaluru City Police · Crime in Bengaluru 2023', 27, '#c4b5fd')
        text(draw, (180, 720), 'Molestation + insulting modesty. Not used to score individual streets.', 27, '#c4b5fd')
    else:
        text(draw, (180, 420), 'SAFEHER', 78, bold=True)
        text(draw, (180, 550), 'More light. More people. More help along the way.', 44)
        text(draw, (180, 660), 'Delhi + Bengaluru · Open data · Gemini on Vertex AI', 30, '#ddd6fe')
        text(draw, (180, 775), 'Better-informed walks. Never a promise of safety.', 34, '#c4b5fd')
    image.save(path)


def composition(key, screenshot, heading, facts):
    """Frame an unaltered real UI crop alongside explicitly editorial evidence."""
    image = Image.new('RGB', SIZE, '#110d1f')
    draw = ImageDraw.Draw(image)
    text(draw, (70, 55), heading, 42, bold=True)
    source = Image.open(CAPTURE / screenshot).convert('RGB')
    source.thumbnail((1200, 690))
    image.paste(source, (55, 165))
    for index, (label, value) in enumerate(facts):
        y = 180 + index * 155
        text(draw, (1300, y), label, 26, '#c4b5fd', width=530)
        text(draw, (1300, y + 48), value, 35, width=530, bold=True)
    path = OUT / f'{key}-frame.png'
    image.save(path)
    return path


def still_clip(image, target, seconds):
    editor.run(['ffmpeg', '-y', '-v', 'error', '-loop', '1', '-i', str(image),
                '-t', str(seconds), '-vf', 'fps=30,format=yuv420p', '-an',
                '-c:v', 'libx264', '-preset', 'fast', '-crf', '18', str(target)])


def main():
    OUT.mkdir(exist_ok=True, parents=True)
    manifest = json.loads((CAPTURE / 'manifest.json').read_text())
    evidence = manifest['evidence']
    assert not evidence.get('failure'), evidence.get('failure')
    assert evidence['pre_trip']['intent'] == 'pre_trip'
    assert evidence['maps_grounding']['sources']
    assert evidence['scope_guard']['intent'] == 'out_of_scope'
    assert evidence['sharing_api']['revocation_get_status'] == 404
    story = json.loads(STORY.read_text())
    assert sum(s['seconds'] for s in story['scenes']) == 174
    editor.OUT = OUT
    editor.STORY = STORY
    editor.overlay = card
    asyncio.run(editor.audio(story))
    for scene in story['scenes']:
        voice_seconds = editor.duration(OUT / 'audio' / f"{scene['key']}.mp3")
        if voice_seconds > scene['seconds'] - 0.6:
            raise ValueError(f"Give the narration breathing room instead of speeding it up: {scene['key']}")
    scenes = {}
    images = {
        'walk_home': CAPTURE / 'score_evidence.png',
        'route_comparison': composition('route_comparison', 'score_evidence.png', '10 PM · Two extra minutes', [
            ('Shortest', '13 min · Score 67'), ('Most support', '15 min · Score 89'),
            ('Mapped as lit', '13% → 61%'), ('Places likely open', '11 → 34')]),
        'score_evidence': CAPTURE / 'score_evidence.png',
        'plan_b': composition('plan_b', 'plan_b.png', 'Plan B · Walking-network access to help', [
            ('Shortest route · max walk', '5.4 min (exact)'), ('Selected Most support · max walk', '2.1 min (UI rounds up)'),
            ('Important limitation', 'Opening hours include estimates')]),
        'late_night': composition('late_night', 'late_night.png', '11:30 PM · The city around her changed', [
            ('Shortest score', '67 → 46'), ('Most support score', '89 → 58'),
            ('Shortest route weak stretch', '653 m (selected route: 133 m)')]),
        'herside_pre_trip': CAPTURE / 'herside_pre_trip.png',
        'maps_grounding': CAPTURE / 'maps_grounding.png',
        'scope_guard': CAPTURE / 'scope_guard.png',
        'sos': CAPTURE / 'sos.png',
    }
    # The app scrolled to local-name tool cards after the grounded response.
    # Present the actual captured reply and citations as an editorial transcript,
    # rather than falsely labelling the unrelated school cards as Maps grounding.
    response = evidence['maps_grounding']['full_response']
    grounded = Image.new('RGB', SIZE, '#110d1f')
    gd = ImageDraw.Draw(grounded)
    text(gd, (100, 65), 'HerSide · Captured Google Maps-grounded response', 40, bold=True)
    text(gd, (100, 155), 'Question: Which cafés near Sri Vani Girls High School stay open after 11 pm?', 31, '#c4b5fd')
    text(gd, (100, 260), response['reply'].split('\n\n')[0], 34, width=1700)
    text(gd, (100, 520), 'Sources returned with the real response (selection)', 28, '#c4b5fd')
    for index, source in enumerate(response['sources'][:4]):
        text(gd, (100, 580 + index * 56), source['title'], 25, width=1700)
    text(gd, (100, 840), 'Editorial transcript of recorded API response · Verify hours before relying on them', 24, '#ddd6fe')
    maps = OUT / 'grounded-transcript.png'
    grounded.save(maps)
    images['maps_grounding'] = maps
    # Frame the real viewer header/status; avoid duplicate capture badges and
    # never suggest the unavailable basemap is a rendered working viewer map.
    source = Image.open(CAPTURE / 'sharing-viewer.png').convert('RGB')
    image = Image.new('RGB', SIZE, '#110d1f')
    draw = ImageDraw.Draw(image)
    text(draw, (150, 100), 'Shared by choice · Latest location received', 46, bold=True)
    region = source.crop((0, 0, 1150, 220))
    region = region.resize((1560, 298))
    image.paste(region, (150, 260))
    text(draw, (150, 630), 'SIMULATED GPS · Real location API update received', 30, '#ddd6fe')
    text(draw, (150, 690), 'Status-only viewer · Basemap not demonstrated in this capture', 28, '#ddd6fe')
    text(draw, (150, 770), 'Stop sharing verified: private viewer access revoked (HTTP 404)', 28, '#c4b5fd')
    sharing = OUT / 'sharing-frame.png'
    image.save(sharing)
    images['sharing'] = sharing
    for scene in story['scenes']:
        key = scene['key']
        if key in ('opening', 'closing'):
            continue
        clip = OUT / f'{key}-verified.mp4'
        still_clip(images[key], clip, scene['seconds'])
        scenes[key] = {'clip': str(clip.relative_to(ROOT)), 'source': str(images[key].relative_to(ROOT))}
    # final_edit intentionally discovers only capture*/manifest.json under OUT.
    build = OUT / 'capture-edit'
    build.mkdir(exist_ok=True)
    (build / 'manifest.json').write_text(json.dumps({
        'scenes': scenes, 'evidence_source': str((CAPTURE / 'manifest.json').relative_to(ROOT)),
        'presentation': 'Verified real UI stills with editorial metric panels; synthetic phone notifications.'}, indent=2))
    editor.assemble(story)
    for suffix in ('mp4', 'srt'):
        shutil.copy2(OUT / f'safeher-final-demo.{suffix}', MEDIA / f'safeher-final-demo.{suffix}')
    print('Current demo and captions published to docs/media.', flush=True)


original_overlay = editor.overlay
if __name__ == '__main__':
    main()
