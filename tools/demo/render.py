#!/usr/bin/env python3
"""Turn a demo recording (frames + events, tools/cockpit/frontend/tests/demo/recorder.ts) into:

- explainer.mp4  the full walkthrough, captions burned in, real time except where the
                 recording asked for a speed-up (shown with a "N×" badge);
- explainer.vtt / explainer.srt  the same captions as subtitle files;
- demo.gif       the short time-lapse for the README (chapter labels only);
- demo.mp4       the same time-lapse as MP4 (much smaller; for a landing page <video>);
- poster.jpg     the frame marked as poster.

Usage:  uv run --with pillow tools/demo/render.py build/demo/<recording> [--out DIR]
        [--fps 25] [--gif-width 800] [--gif-fps 8] [--gif-seconds 45] [--only explainer|gif]
Needs ffmpeg (with libx264) on PATH.
"""

from __future__ import annotations

import argparse
import bisect
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# --- timeline -------------------------------------------------------------------------------


@dataclass
class Piece:
    """Recording time [start, end) played at `speed` (> 0)."""

    start: float
    end: float
    speed: float

    @property
    def out_duration(self) -> float:
        return (self.end - self.start) / self.speed


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def pieces_from(events: list[dict], kind: str, default: float, end: float) -> list[Piece]:
    """Pieces of the timeline from the `kind` events ("speed" or "gif"). An x of false/0
    cuts the time until the next event of that kind."""
    marks = [(0.0, default)] + [(e["t"], e["x"]) for e in events if e["type"] == kind]
    pieces: list[Piece] = []
    for (t, x), (t_next, _) in zip(marks, marks[1:] + [(end, None)], strict=False):
        t_next = min(t_next, end)
        if t_next <= t or not x:
            continue
        if pieces and pieces[-1].speed == x and abs(pieces[-1].end - t) < 1e-9:
            pieces[-1].end = t_next
        else:
            pieces.append(Piece(t, t_next, float(x)))
    return pieces


def fit_chapters(pieces: list[Piece], events: list[dict], end: float, total: float) -> list[Piece]:
    """Speed up the longest chapters until the whole fits in `total` seconds: every chapter
    gets at most the same cap (found by bisection), shorter ones keep their speed."""
    cuts = sorted({0.0, end, *(e["t"] for e in events if e["type"] == "chapter")})
    spans = list(zip(cuts, cuts[1:], strict=False))
    # Split the pieces at the chapter boundaries.
    split: list[tuple[int, Piece]] = []
    for p in pieces:
        for i, (a, b) in enumerate(spans):
            lo, hi = max(p.start, a), min(p.end, b)
            if hi > lo:
                split.append((i, Piece(lo, hi, p.speed)))
    natural = [sum(p.out_duration for j, p in split if j == i) for i in range(len(spans))]
    lo, hi = 0.0, max(natural)
    for _ in range(60):
        cap = (lo + hi) / 2
        if sum(min(d, cap) for d in natural) > total:
            hi = cap
        else:
            lo = cap
    cap = lo
    return [Piece(p.start, p.end, p.speed * max(1.0, natural[i] / cap)) for i, p in split if cap]


class Timeline:
    def __init__(self, pieces: list[Piece]):
        self.pieces = pieces
        self.out_starts: list[float] = []
        acc = 0.0
        for p in pieces:
            self.out_starts.append(acc)
            acc += p.out_duration
        self.duration = acc

    def rec_time(self, out_t: float) -> tuple[float, float]:
        """(recording time, speed) shown at output time `out_t`."""
        i = max(0, bisect.bisect_right(self.out_starts, out_t) - 1)
        p = self.pieces[i]
        return min(p.end, p.start + (out_t - self.out_starts[i]) * p.speed), p.speed

    def out_time(self, rec_t: float) -> float | None:
        """Output time of recording time `rec_t`, or None if that time is cut."""
        for p, start in zip(self.pieces, self.out_starts, strict=False):
            if p.start <= rec_t < p.end:
                return start + (rec_t - p.start) / p.speed
        if self.pieces and rec_t >= self.pieces[-1].end:
            return self.duration
        return None

    def out_time_after(self, rec_t: float) -> float:
        """Output time of `rec_t`, or of the first kept moment after it."""
        t = self.out_time(rec_t)
        if t is not None:
            return t
        later = [s for p, s in zip(self.pieces, self.out_starts, strict=False) if p.start >= rec_t]
        return later[0] if later else self.duration


def state_at(events: list[dict], kind: str, rec_t: float, default):
    value = default
    for e in events:
        if e["t"] > rec_t:
            break
        if e["type"] == kind:
            value = e
    return value


# --- drawing --------------------------------------------------------------------------------

FONT_CANDIDATES = {
    "regular": [
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/Library/Fonts/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ],
    "bold": [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/Library/Fonts/Arial Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ],
}


def font(kind: str, size: int) -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES[kind]:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)


BLUE, PURPLE = (13, 110, 253), (111, 66, 193)


def gradient(w: int, h: int, alpha: int) -> Image.Image:
    g = Image.new("RGBA", (w, h))
    px = g.load()
    for x in range(w):
        f = x / max(1, w - 1)
        c = tuple(round(BLUE[i] + (PURPLE[i] - BLUE[i]) * f) for i in range(3))
        for y in range(h):
            px[x, y] = (*c, alpha)
    return g


def wrap(draw: ImageDraw.ImageDraw, text: str, fnt, max_w: int) -> list[str]:
    lines: list[str] = []
    for para in text.split("\n"):
        line = ""
        for word in para.split():
            trial = f"{line} {word}".strip()
            if draw.textlength(trial, font=fnt) <= max_w or not line:
                line = trial
            else:
                lines.append(line)
                line = word
        lines.append(line)
    return lines


class Overlay:
    """Draws captions, chapter labels and the speed badge; layers are cached by content."""

    def __init__(self, size: tuple[int, int], style: str):
        self.w, self.h = size
        self.style = style  # "explainer" | "gif"
        s = self.h / 1080
        self.s = s
        self.f_caption = font("regular", round(34 * s))
        self.f_chapter = font("bold", round(24 * s if style == "explainer" else 34 * s))
        self.f_badge = font("bold", round(26 * s if style == "explainer" else 34 * s))
        self.cache: dict[tuple, Image.Image] = {}

    def layer(self, chapter: dict | None, caption: str, speed: float) -> Image.Image | None:
        key = (chapter and chapter.get("index"), caption, round(speed, 2))
        if key in self.cache:
            return self.cache[key]
        if not caption and not chapter and speed < 1.5:
            self.cache[key] = None
            return None
        img = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        s, pad, margin = self.s, round(22 * self.s), round(36 * self.s)
        label = f"{chapter['index']}/{chapter['total']}  {chapter['title']}" if chapter else ""
        if self.style == "explainer" and (caption or label):
            max_w = round(self.w * 0.74)
            lines = wrap(d, caption, self.f_caption, max_w) if caption else []
            line_h = round(self.f_caption.size * 1.32)
            chap_h = round(self.f_chapter.size * 1.5) if label else 0
            text_w = max(
                [d.textlength(x, font=self.f_caption) for x in lines]
                + [d.textlength(label, font=self.f_chapter)]
            )
            box_w, box_h = (
                round(text_w + 2 * pad),
                chap_h + len(lines) * line_h + 2 * pad - (round(6 * s) if lines else 0),
            )
            x0, y0 = (self.w - box_w) // 2, self.h - margin - box_h
            d.rounded_rectangle(
                (x0, y0, x0 + box_w, y0 + box_h), radius=round(14 * s), fill=(12, 16, 30, 205)
            )
            d.rounded_rectangle(
                (x0, y0, x0 + round(6 * s), y0 + box_h), radius=round(3 * s), fill=(*PURPLE, 255)
            )
            y = y0 + pad
            if label:
                d.text((x0 + pad, y), label.upper(), font=self.f_chapter, fill=(150, 175, 255, 255))
                y += chap_h
            for line in lines:
                d.text((x0 + pad, y), line, font=self.f_caption, fill=(255, 255, 255, 255))
                y += line_h
        if self.style == "gif" and label:
            tw = d.textlength(label, font=self.f_chapter)
            bw, bh = round(tw + 2 * pad), round(self.f_chapter.size * 1.9)
            x0, y0 = margin, self.h - margin - bh
            g = gradient(bw, bh, 235)
            mask = Image.new("L", (bw, bh), 0)
            ImageDraw.Draw(mask).rounded_rectangle(
                (0, 0, bw - 1, bh - 1), radius=round(12 * s), fill=255
            )
            img.paste(g, (x0, y0), mask)
            d.text(
                (x0 + pad, y0 + (bh - self.f_chapter.size) // 2 - round(2 * s)),
                label,
                font=self.f_chapter,
                fill="white",
            )
        if speed >= 1.5 and self.style == "explainer":
            badge = f"▶▶ {speed:g}×"
            if self.f_badge.getbbox("▶")[2] <= 0:  # font without the arrow glyph
                badge = f">> {speed:g}x"
            tw = d.textlength(badge, font=self.f_badge)
            bw, bh = round(tw + 1.4 * pad), round(self.f_badge.size * 1.7)
            x0 = self.w - margin - bw
            y0 = round(110 * s)  # below the cockpit's top bar
            d.rounded_rectangle(
                (x0, y0, x0 + bw, y0 + bh), radius=round(10 * s), fill=(12, 16, 30, 200)
            )
            d.text(
                (x0 + 0.7 * pad, y0 + (bh - self.f_badge.size) // 2 - round(2 * s)),
                badge,
                font=self.f_badge,
                fill=(255, 210, 90, 255),
            )
        self.cache[key] = img
        return img


# --- rendering ------------------------------------------------------------------------------


class Frames:
    def __init__(self, rec: Path, size: tuple[int, int]):
        rows = load_jsonl(rec / "frames.jsonl")
        if not rows:
            sys.exit(f"{rec}: no frames recorded")
        self.times = [r["t"] for r in rows]
        self.files = [rec / "frames" / r["f"] for r in rows]
        self.size = size
        self._last: tuple[int, Image.Image] | None = None

    def at(self, rec_t: float) -> Image.Image:
        i = max(0, bisect.bisect_right(self.times, rec_t) - 1)
        if not self._last or self._last[0] != i:
            img = Image.open(self.files[i]).convert("RGB")
            if img.size != self.size:
                img = img.resize(self.size, Image.LANCZOS)
            self._last = (i, img)
        return self._last[1]


def ffmpeg_writer(out: Path, size: tuple[int, int], fps: int, crf: int, scale_w: int | None = None):
    vf = ["format=yuv420p"]
    if scale_w:
        vf.insert(0, f"scale={scale_w}:-2:flags=lanczos")
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{size[0]}x{size[1]}",
        "-r", str(fps), "-i", "-",
        "-vf", ",".join(vf), "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf),
        "-movflags", "+faststart", str(out),
    ]  # fmt: skip
    return subprocess.Popen(cmd, stdin=subprocess.PIPE)


def render(
    rec: Path,
    events: list[dict],
    frames: Frames,
    timeline: Timeline,
    style: str,
    out: Path,
    fps: int,
    crf: int,
    scale_w: int | None = None,
) -> None:
    overlay = Overlay(frames.size, style)
    proc = ffmpeg_writer(out, frames.size, fps, crf, scale_w)
    n = int(timeline.duration * fps)
    last_key, last_bytes = None, b""
    for k in range(n):
        rec_t, speed = timeline.rec_time(k / fps)
        chapter = state_at(events, "chapter", rec_t, None)
        if chapter is not None and not chapter.get("index"):
            chapter = None  # rec.clear(): the closing card has no chapter label
        caption = (
            state_at(events, "caption", rec_t, {"text": ""})["text"] if style == "explainer" else ""
        )
        base_i = max(0, bisect.bisect_right(frames.times, rec_t) - 1)
        key = (base_i, chapter and chapter["index"], caption, speed)
        if key != last_key:
            img = frames.at(rec_t)
            layer = overlay.layer(chapter, caption, speed)
            if layer is not None:
                img = Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB")
            last_key, last_bytes = key, img.tobytes()
        proc.stdin.write(last_bytes)
        if k % (fps * 30) == 0:
            print(f"  {style}: {k / fps:6.0f} s / {timeline.duration:.0f} s", flush=True)
    proc.stdin.close()
    if proc.wait() != 0:
        sys.exit(f"ffmpeg failed for {out}")


def fmt_ts(t: float, sep: str) -> str:
    ms = round(t * 1000)
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def subtitles(events: list[dict], timeline: Timeline, end: float) -> list[tuple[float, float, str]]:
    caps = [e for e in events if e["type"] == "caption"]
    cues = []
    for e, nxt in zip(caps, caps[1:] + [{"t": end}], strict=False):
        if not e["text"]:
            continue
        a, b = timeline.out_time_after(e["t"]), timeline.out_time_after(nxt["t"])
        if b - a >= 0.2:
            cues.append((a, b, e["text"]))
    return cues


def write_subs(cues, out_dir: Path) -> None:
    vtt = ["WEBVTT", ""]
    srt = []
    for i, (a, b, text) in enumerate(cues, 1):
        vtt += [f"{fmt_ts(a, '.')} --> {fmt_ts(b, '.')}", text, ""]
        srt += [str(i), f"{fmt_ts(a, ',')} --> {fmt_ts(b, ',')}", text, ""]
    (out_dir / "explainer.vtt").write_text("\n".join(vtt))
    (out_dir / "explainer.srt").write_text("\n".join(srt))


def make_gif(src_mp4: Path, out_gif: Path, width: int, fps: int, colors: int) -> None:
    graph = (
        f"fps={fps},scale={width}:-2:flags=lanczos,split[a][b];"
        f"[a]palettegen=max_colors={colors}:stats_mode=diff[p];"
        f"[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle"
    )
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src_mp4),
         "-filter_complex", graph, "-loop", "0", str(out_gif)],
        check=True,
    )  # fmt: skip


def main() -> None:
    ap = argparse.ArgumentParser(description="Render the demo videos from a recording.")
    ap.add_argument("recording", type=Path)
    ap.add_argument("--out", type=Path, help="output directory (default: <recording>/out)")
    ap.add_argument("--fps", type=int, default=25)
    ap.add_argument("--gif-width", type=int, default=800)
    ap.add_argument("--gif-fps", type=int, default=8)
    ap.add_argument("--gif-colors", type=int, default=96)
    ap.add_argument(
        "--gif-seconds",
        type=float,
        default=45,
        help="longest time-lapse; all GIF speeds are raised evenly to fit (0: no limit)",
    )
    ap.add_argument("--only", choices=["explainer", "gif"])
    args = ap.parse_args()
    if not shutil.which("ffmpeg"):
        sys.exit("ffmpeg is missing (macOS: brew install ffmpeg)")

    rec = args.recording
    if not (rec / "meta.json").is_file():
        found = sorted(p.parent for p in Path("build/demo").glob("*/meta.json"))
        hint = (
            "\n".join(f"  {p}" for p in found[-5:]) or "  (none; record one with make demo-video)"
        )
        sys.exit(f"{rec} is not a recording (no meta.json). Recordings:\n{hint}")
    out = args.out or rec / "out"
    out.mkdir(parents=True, exist_ok=True)
    meta = json.loads((rec / "meta.json").read_text())
    size = (meta["size"]["width"], meta["size"]["height"])
    events = load_jsonl(rec / "events.jsonl")
    frames = Frames(rec, size)
    end = next((e["t"] for e in events if e["type"] == "end"), frames.times[-1])

    poster = next((e["t"] for e in events if e["type"] == "poster"), None)
    if poster is not None:
        frames.at(poster).save(out / "poster.jpg", quality=90)

    if args.only in (None, "explainer"):
        tl = Timeline(pieces_from(events, "speed", 1.0, end))
        print(f"explainer: {tl.duration:.0f} s from {end:.0f} s of recording")
        render(rec, events, frames, tl, "explainer", out / "explainer.mp4", args.fps, crf=20)
        write_subs(subtitles(events, tl, end), out)

    if args.only in (None, "gif"):
        pieces = pieces_from(events, "gif", 6.0, end)
        natural = Timeline(pieces).duration
        if args.gif_seconds and natural > args.gif_seconds:
            pieces = fit_chapters(pieces, events, end, args.gif_seconds)
            print(f"time-lapse: {natural:.0f} s at the storyboard's speeds, fitted per chapter")
        tl = Timeline(pieces)
        print(f"time-lapse: {tl.duration:.0f} s")
        chapters = [{"t": 0.0, "title": "intro"}] + [e for e in events if e["type"] == "chapter"]
        for c, nxt in zip(chapters, chapters[1:] + [{"t": end}], strict=True):
            span = tl.out_time_after(nxt["t"]) - tl.out_time_after(c["t"])
            print(f"    {c.get('title', '?'):24s} {span:5.1f} s")
        render(
            rec, events, frames, tl, "gif", out / "demo.mp4", args.gif_fps * 2, crf=24, scale_w=1280
        )
        make_gif(out / "demo.mp4", out / "demo.gif", args.gif_width, args.gif_fps, args.gif_colors)

    for f in sorted(out.iterdir()):
        print(f"  {f.name:16s} {f.stat().st_size / 1e6:7.1f} MB")


if __name__ == "__main__":
    main()
