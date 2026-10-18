"""Merge a validated support-only capture into the main edit manifest."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

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
    # Keep the viewer's real status, explicitly label the missing basemap.
    viewer = folder / 'viewer-status.mp4'
    subprocess.run([
        'ffmpeg', '-y', '-v', 'error', '-loop', '1', '-i', str(folder / 'sharing-viewer.png'),
        '-t', '5', '-an', '-vf',
        'crop=1150:220:0:0,scale=1920:-2,pad=1920:1080:0:160:color=0x110d1f,fps=30',
        '-c:v', 'libx264', '-preset', 'fast', '-crf', '20', str(viewer)], check=True)
    concat = folder / 'edit-concat.txt'
    concat.write_text('\n'.join(f"file '{p}'" for p in [active, viewer, stopped]))
    sharing = folder / 'sharing-edit.mp4'
    subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'concat', '-safe', '0',
                    '-i', str(concat), '-c', 'copy', str(sharing)], check=True)
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
