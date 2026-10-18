"""Synthesize scene narration and assemble a duration-checked, captioned demo.

Build the current demo with: uv run --no-sync python demo/build_demo.py
This module supplies shared voice, caption and encoding utilities.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import hashlib
import subprocess
from pathlib import Path

import edge_tts
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "demo/out/final"
STORY = ROOT / "demo/final_story.json"
FONT = "/System/Library/Fonts/Supplemental/Arial.ttf"
BOLD = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
WIDTH, HEIGHT = 1920, 1080


def run(args: list[str]) -> None:
    subprocess.run(args, check=True)


def duration(path: Path) -> float:
    return float(subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=nw=1:nk=1", str(path)], text=True).strip())


async def audio(story: dict) -> None:
    folder = OUT / "audio"
    folder.mkdir(parents=True, exist_ok=True)
    for scene in story["scenes"]:
        path = folder / f"{scene['key']}.mp3"
        digest = hashlib.sha256((scene['narration'] + story['voice'] + story['voice_rate']).encode()).hexdigest()
        signature = path.with_suffix('.sha256')
        if not path.exists() or path.stat().st_size < 100 or not signature.exists() or signature.read_text() != digest:
            tts = edge_tts.Communicate(
                scene["narration"], story["voice"], rate=story["voice_rate"],
                proxy="http://sysproxy.wal-mart.com:8080")
            await tts.save(str(path))
            signature.write_text(digest)
        measured = duration(path)
        print(f"{scene['key']}: voice {measured:.2f}s / slot {scene['seconds']}s", flush=True)
        if measured > scene["seconds"] * 1.18:
            raise ValueError(f"Narration too long for {scene['key']}; edit script instead of rushing")


def wrap_pixels(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont,
                max_width: int) -> list[str]:
    lines, line = [], ""
    for word in text.split():
        candidate = f"{line} {word}".strip()
        if draw.textlength(candidate, font=font) > max_width and line:
            lines.append(line)
            line = word
        else:
            line = candidate
    if line:
        lines.append(line)
    return lines


def overlay(scene: dict, path: Path, card: bool = False) -> None:
    image = Image.new("RGBA", (WIDTH, HEIGHT), (17, 13, 31, 255) if card else (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    if card:
        for radius in range(480, 40, -8):
            alpha = max(2, int((480-radius) / 40))
            draw.ellipse((1330-radius, 360-radius, 1330+radius, 360+radius),
                         fill=(63 + alpha, 35, 98 + alpha, 255))
        draw.text((130, 150), "SAFEHER", font=ImageFont.truetype(BOLD, 40), fill="#c4b5fd")
        title_font = ImageFont.truetype(BOLD, 78)
        for index, line in enumerate(wrap_pixels(draw, scene["heading"], title_font, 1350)):
            draw.text((130, 320 + index * 100), line, font=title_font, fill="white")
        sub = "OPEN DATA  +  GOOGLE MAPS  +  GEMINI ON VERTEX AI"
        draw.text((130, 750), sub, font=ImageFont.truetype(FONT, 29), fill="#d1c4e9")
        draw.rounded_rectangle((130, 830, 1510, 900), radius=12, fill="#262036")
        draw.text((155, 848), "Better-informed walks. Never a guarantee of safety.",
                  font=ImageFont.truetype(FONT, 30), fill="#ffffff")
    else:
        # Reserve a band below a scaled 1920x900 UI; don't cover map controls or evidence.
        draw.rectangle((0, 900, WIDTH, HEIGHT), fill=(17, 13, 31, 255))
        draw.rectangle((48, 935, 55, 1035), fill="#c4b5fd")
        font = ImageFont.truetype(BOLD, 36)
        for index, line in enumerate(wrap_pixels(draw, scene["heading"], font, 1680)):
            draw.text((82, 940 + index * 45), line, font=font, fill="white")
        draw.text((82, 1006), 'SIMULATED GPS · Viewer basemap unavailable on recording network' if scene['key'] == 'simulated_gps_location_sharing' else 'SafeHer · Evidence-led walking decisions', font=ImageFont.truetype(FONT, 24), fill='#d1c4e9')
    image.save(path)


def subtitle_chunks(text: str, words_per_chunk: int = 9) -> list[str]:
    words = text.split()
    return [" ".join(words[i:i + words_per_chunk]) for i in range(0, len(words), words_per_chunk)]


def stamp(seconds: float) -> str:
    ms = round(seconds * 1000)
    return f"{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02},{ms%1000:03}"


def assemble(story: dict) -> None:
    manifests = sorted(OUT.glob('capture*/manifest.json'), key=lambda p: p.stat().st_mtime)
    if not manifests:
        raise ValueError('No successful capture manifest; run capture_demo.py first')
    capture = json.loads(manifests[-1].read_text())
    edits = OUT / "edit"
    edits.mkdir(exist_ok=True)
    clips, subtitles, elapsed, events = [], [], 0.0, []
    for scene in story["scenes"]:
        key, seconds = scene["key"], scene["seconds"]
        card = key in ("opening", "closing")
        image = edits / f"{key}.png"
        overlay(scene, image, card)
        voice = OUT / "audio" / f"{key}.mp3"
        length = duration(voice)
        audio_speed = max(1.0, length / (seconds - .6))
        if audio_speed > 1.18:
            raise ValueError(f"Voice requires excessive speed-up: {key}")
        target = edits / f"{key}.mp4"
        args = ["ffmpeg", "-y", "-v", "error"]
        if card:
            args += ["-loop", "1", "-i", str(image), "-i", str(voice)]
            vf = "[0:v]fps=30,format=yuv420p[v]"
            voice_index = 1
            source_seconds = seconds
        else:
            item = capture["scenes"].get(key)
            if not item or item.get("skipped"):
                raise ValueError(f"No verified capture for {key}; revise story rather than invent footage")
            source = ROOT / item["clip"]
            source_seconds = duration(source)
            # Compress the entire useful scene, then hold the final result if short.
            video_speed = max(1.0, source_seconds / (seconds - 1))
            args += ["-i", str(source), "-loop", "1", "-i", str(image), "-i", str(voice)]
            vf = (f"[0:v]setpts=(PTS-STARTPTS)/{video_speed:.6f},"
                  f"scale={WIDTH}:900:force_original_aspect_ratio=decrease,"
                  f"pad={WIDTH}:900:(ow-iw)/2:(oh-ih)/2:color=0x110d1f,"
                  f"pad={WIDTH}:{HEIGHT}:0:0:color=0x110d1f,"
                  f"tpad=stop_mode=clone:stop_duration={seconds},fps=30[ui];"
                  "[ui][1:v]overlay=0:0:format=auto,format=yuv420p[v]")
            voice_index = 2
        af = (f"[{voice_index}:a]atempo={audio_speed:.6f},"
              f"apad=whole_dur={seconds},atrim=duration={seconds},"
              "loudnorm=I=-16:TP=-1.5:LRA=9[a]")
        args += ["-filter_complex", vf + ";" + af, "-map", "[v]", "-map", "[a]",
                 "-t", str(seconds), "-c:v", "libx264", "-preset", "fast", "-crf", "20",
                 "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", str(target)]
        run(args)
        clips.append(target)
        chunks = subtitle_chunks(scene["narration"])
        voiced = min(seconds, length / audio_speed)
        for index, chunk in enumerate(chunks):
            start = elapsed + voiced * index / len(chunks)
            end = elapsed + voiced * (index + 1) / len(chunks)
            subtitles.append(f"{len(subtitles)+1}\n{stamp(start)} --> {stamp(end)}\n{chunk}\n")
        events.append({"scene": key, "start": elapsed, "seconds": seconds,
                       "original_video_seconds": source_seconds, "voice_speed": audio_speed})
        elapsed += seconds
        print(f"Rendered {key}: {seconds}s", flush=True)
    if elapsed >= 180:
        raise ValueError("Demo must be strictly below three minutes")
    listing = edits / "concat.txt"
    listing.write_text("\n".join(f"file '{p}'" for p in clips))
    srt = OUT / "safeher-final-demo.srt"
    srt.write_text("\n".join(subtitles))
    staged = OUT / "safeher-final-demo.mp4"
    run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(listing),
         "-i", str(srt), "-map", "0:v", "-map", "0:a", "-map", "1:0", "-c", "copy",
         "-c:s", "mov_text", "-metadata:s:s:0", "language=eng", "-movflags", "+faststart", str(staged)])
    actual = duration(staged)
    if actual >= 180:
        raise ValueError(f"Encoded output over limit: {actual}s")
    run(["ffmpeg", "-v", "error", "-i", str(staged), "-f", "null", "-"])
    (OUT / "edit-manifest.json").write_text(json.dumps({
        "duration_seconds": actual, "voice": story["voice"], "scenes": events,
        "captions": "scene-timed approximate SRT + selectable MP4 subtitle track",
        "capture": capture}, indent=2))
    print(f"Validated output: {staged} ({actual:.3f}s)", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio-only", action="store_true")
    args = parser.parse_args()
    story = json.loads(STORY.read_text())
    assert sum(s["seconds"] for s in story["scenes"]) == story["target_seconds"] < 180
    asyncio.run(audio(story))
    if not args.audio_only:
        assemble(story)


if __name__ == "__main__":
    main()
