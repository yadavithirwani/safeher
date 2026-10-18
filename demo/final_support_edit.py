"""Merge a validated support-only capture into the main edit manifest."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'demo/out/final'


def cut(source: Path, target: Path, start: float, seconds: float) -> None:
    subprocess.run([
        'ffmpeg', '-y', '-v', 'error', '-i', str(source), '-ss', str(max(0, start)),
        '-t', str(seconds), '-an', '-vf', 'fps=30', '-c:v', 'libx264',
        '-preset', 'fast', '-crf', '20', str(target)], check=True)


def main() -> None:
    manifests = sorted(OUT.glob('support-*/support-manifest.json'))
    support = next((p for p in reversed(manifests)
                    if json.loads(p.read_text()).get('status') == 'success'), None)
    if not support:
        raise RuntimeError('No successful support capture')
    data = json.loads(support.read_text())
    folder = support.parent
    if not data.get('revocation_get', {}).get('revoked'):
        raise RuntimeError('Sharing revocation not confirmed')
    # Use actual active sender footage, not the stale initial attempt.
    active = folder / 'active.mp4'
    stopped = folder / 'stopped.mp4'
    sos = folder / 'sos.mp4'
    stop_at = data['sender_stop_seconds']
    viewer_at = data.get('viewer_page_offset_seconds')
    if viewer_at is None:
        raise RuntimeError('Per-page timeline missing; use the latest recorder')
    cut(folder / 'sender.webm', active, max(0, viewer_at - 5), 5)
    cut(folder / 'sender.webm', stopped, stop_at + 1, 3)
    subprocess.run(['ffmpeg', '-y', '-v', 'error', '-loop', '1', '-i', str(folder / 'sos.png'), '-t', '6', '-vf', 'fps=30,format=yuv420p', '-c:v', 'libx264', '-preset', 'fast', '-crf', '20', str(sos)], check=True)
    # Present the real viewer status in a deliberate full-frame layout, not a
    # partial-height browser crop that looks like a broken video transition.
    viewer_image = folder / 'viewer-presentation.png'
    background = Image.open(folder / 'sharing-sender.png').convert('RGBA')
    background = Image.alpha_composite(background, Image.new('RGBA', background.size, (17, 13, 31, 225)))
    draw = ImageDraw.Draw(background)
    font_path = '/System/Library/Fonts/Supplemental/Arial.ttf'
    title_font = ImageFont.truetype(font_path, 48)
    body_font = ImageFont.truetype(font_path, 28)
    draw.text((200, 200), 'Trusted contact · Latest location received', font=title_font, fill='white')
    source = Image.open(folder / 'sharing-viewer.png').convert('RGB')
    status = source.crop((0, 0, 1150, 220))
    status.thumbnail((1520, 420))
    status = status.resize((1520, round(status.height * 1520 / status.width)))
    draw.rounded_rectangle((184, 330, 1736, 370 + status.height), radius=20, fill='white')
    background.paste(status, (200, 350))
    draw.text((200, 760), 'SIMULATED GPS · Real sharing API response', font=body_font, fill='#ddd6fe')
    draw.text((200, 810), 'Viewer basemap unavailable on recording network.', font=body_font, fill='#ddd6fe')
    background.convert('RGB').save(viewer_image)
    viewer = folder / 'viewer-status.mp4'
    subprocess.run([
        'ffmpeg', '-y', '-v', 'error', '-loop', '1', '-i', str(viewer_image),
        '-t', '5', '-an', '-vf', 'fps=30,format=yuv420p',
        '-c:v', 'libx264', '-preset', 'fast', '-crf', '20', str(viewer)], check=True)
    sharing = folder / 'sharing-edit.mp4'
    # Decode and normalize all segments; avoid stream-copy joins between
    # independently encoded screenshots and browser footage.
    subprocess.run([
        'ffmpeg', '-y', '-v', 'error', '-i', str(active), '-i', str(viewer), '-i', str(stopped),
        '-filter_complex',
        '[0:v]settb=AVTB,setpts=PTS-STARTPTS,fps=30,format=yuv420p[a];'
        '[1:v]settb=AVTB,setpts=PTS-STARTPTS,fps=30,format=yuv420p[b];'
        '[2:v]settb=AVTB,setpts=PTS-STARTPTS,fps=30,format=yuv420p[c];'
        '[a][b]xfade=transition=fade:duration=0.35:offset=4.65[ab];'
        '[ab][c]xfade=transition=fade:duration=0.35:offset=9.3[v]',
        '-map', '[v]', '-an', '-c:v', 'libx264', '-preset', 'fast', '-crf', '20',
        '-pix_fmt', 'yuv420p', '-movflags', '+faststart', str(sharing)], check=True)
    base = OUT / 'capture-20261018-130038/manifest.json'
    manifest = json.loads(base.read_text())
    manifest['scenes']['simulated_gps_location_sharing'] = {
        'clip': str(sharing.relative_to(ROOT)), 'viewer_map_available': False,
        'note': 'Real status-only viewer; basemap blocked by recording network. Synthetic GPS labeled.'}
    manifest['scenes']['112_information_dialog'] = {'clip': str(sos.relative_to(ROOT))}
    manifest['support_validation'] = {
        'manifest': str(support.relative_to(ROOT)), 'revoked': True,
        'viewer_location_received': True, 'viewer_map_available': False}
    base.write_text(json.dumps(manifest, indent=2))
    print('Support scenes merged; full editor can now run.', flush=True)


if __name__ == '__main__':
    main()
