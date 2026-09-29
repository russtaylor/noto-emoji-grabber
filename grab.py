#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pillow>=10.0"]
# ///
"""Download Noto animated emoji and convert them to Slack-friendly GIFs.

Source of truth: the JSON index behind https://googlefonts.github.io/noto-emoji-animation/
Assets:          https://fonts.gstatic.com/s/e/notoemoji/latest/<codepoint>/512.gif

Shrink strategy (stop as soon as the file is <= --max-bytes):
  1. Scale down: 128 -> 112 -> 96 -> 80 -> 64 px
  2. At the minimum size, drop frames (keep every 2nd, 3rd, ... frame),
     stretching durations so playback speed stays the same.
  3. If gifsicle is on PATH, each candidate is also run through
     `gifsicle -O3 --lossy` which usually lets a larger size fit.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageSequence

API_URL = "https://googlefonts.github.io/noto-emoji-animation/data/api.json"
GIF_URL = "https://fonts.gstatic.com/s/e/notoemoji/latest/{cp}/512.gif"
SIZES = (128, 112, 96, 80, 64)
FRAME_STEPS = (1, 2, 3, 4, 5, 6)
ALPHA_THRESHOLD = 128
UA = {"User-Agent": "noto-emoji-grabber/1.0"}


def slugify(tag: str) -> str:
    """':Paw prints:' -> 'paw-prints', ':piñata:' -> 'pinata', ':up!:' -> 'up'."""
    s = unicodedata.normalize("NFKD", tag.strip(":")).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9_-]+", "-", s.lower())
    return re.sub(r"-{2,}", "-", s).strip("-")


@dataclass
class Emoji:
    codepoint: str
    slugs: list[str]

    @property
    def slug(self) -> str:
        return self.slugs[0]


def fetch(url: str) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
        return r.read()


def load_index() -> list[Emoji]:
    data = json.loads(fetch(API_URL))
    out = []
    for icon in data["icons"]:
        slugs = [s for s in (slugify(t) for t in icon.get("tags", [])) if s]
        out.append(Emoji(icon["codepoint"], slugs or [icon["codepoint"]]))
    return out


def load_existing(path: Path, prefix: str) -> set[str]:
    text = path.read_text(encoding="utf-8")
    pat = re.compile(r":" + re.escape(prefix) + r"([a-z0-9_-]+):", re.I)
    return {m.lower() for m in pat.findall(text)}


# ---------------------------------------------------------------- conversion


def load_frames(raw: bytes) -> list[tuple[Image.Image, int]]:
    """Return fully-composited RGBA frames with their durations (ms)."""
    im = Image.open(io.BytesIO(raw))
    frames = []
    for f in ImageSequence.Iterator(im):
        frames.append((f.convert("RGBA"), int(f.info.get("duration", im.info.get("duration", 40)) or 40)))
    return frames


def to_palette(frame: Image.Image) -> Image.Image:
    """RGBA -> P with index 255 reserved for binary transparency."""
    alpha = frame.getchannel("A")
    rgb = Image.new("RGB", frame.size, (0, 0, 0))
    rgb.paste(frame, mask=alpha)
    p = rgb.quantize(colors=255, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    pal = (p.getpalette() or [])[: 255 * 3]
    p.putpalette(pal + [0] * (768 - len(pal)))
    mask = alpha.point(lambda a: 255 if a < ALPHA_THRESHOLD else 0)
    p.paste(255, mask=mask)
    p.info["transparency"] = 255
    return p


def encode(frames: list[tuple[Image.Image, int]], size: int, step: int) -> bytes:
    picked = []
    for i in range(0, len(frames), step):
        chunk = frames[i : i + step]
        picked.append((chunk[0][0], sum(d for _, d in chunk)))
    pal_frames = [to_palette(f.resize((size, size), Image.Resampling.LANCZOS)) for f, _ in picked]
    buf = io.BytesIO()
    pal_frames[0].save(
        buf,
        format="GIF",
        save_all=True,
        append_images=pal_frames[1:],
        duration=[d for _, d in picked],
        loop=0,
        disposal=2,
        transparency=255,
        optimize=True,
    )
    return buf.getvalue()


def gifsicle(data: bytes, lossy: int) -> bytes:
    exe = shutil.which("gifsicle")
    if not exe:
        return data
    with tempfile.NamedTemporaryFile(suffix=".gif") as src:
        src.write(data)
        src.flush()
        res = subprocess.run(
            [exe, "-O3", f"--lossy={lossy}", "--no-comments", "--no-extensions", src.name],
            capture_output=True,
        )
    return res.stdout if res.returncode == 0 and res.stdout and len(res.stdout) < len(data) else data


def shrink(raw: bytes, max_bytes: int, min_size: int, lossy: int):
    frames = load_frames(raw)
    sizes = [s for s in SIZES if s >= min_size] or [min_size]
    best = None
    ladder = [(s, 1) for s in sizes] + [(sizes[-1], st) for st in FRAME_STEPS[1:]]
    for size, step in ladder:
        if step > 1 and len(frames) // step < 2:
            break
        data = gifsicle(encode(frames, size, step), lossy)
        if best is None or len(data) < len(best[0]):
            best = (data, size, step)
        if len(data) <= max_bytes:
            return data, size, step, True
    return (*best, False)


# ---------------------------------------------------------------- main


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--existing", type=Path, default=Path("existing-noto-emoji.txt"),
                    help="file containing already-uploaded :noto-xxx: names")
    ap.add_argument("--out", type=Path, default=Path("out"))
    ap.add_argument("--cache", type=Path, default=Path(".cache"))
    ap.add_argument("--prefix", default="noto-")
    ap.add_argument("--max-bytes", type=int, default=120_000)
    ap.add_argument("--min-size", type=int, default=64)
    ap.add_argument("--lossy", type=int, default=40, help="gifsicle --lossy level (only if gifsicle installed)")
    ap.add_argument("--only", nargs="*", help="only process these slugs (ignores --existing)")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--list", action="store_true", help="just list missing emoji, don't download")
    args = ap.parse_args()

    index = load_index()
    if args.only:
        wanted = {slugify(s) for s in args.only}
        todo = [e for e in index if wanted & set(e.slugs)]
    else:
        existing = load_existing(args.existing, args.prefix) if args.existing.exists() else set()
        todo = [e for e in index if not (existing & set(e.slugs))]
        matched = {s for e in index for s in e.slugs} & existing
        print(f"index: {len(index)}  existing: {len(existing)} (matched {len(matched)})  missing: {len(todo)}")
        if unmatched := sorted(existing - matched):
            print(f"note: existing names not in index (renamed/removed/custom): {', '.join(unmatched)}")
    if args.limit:
        todo = todo[: args.limit]

    if args.list:
        for e in todo:
            print(f"{args.prefix}{e.slug}\t{e.codepoint}")
        return 0

    if not shutil.which("gifsicle"):
        print("note: gifsicle not found; `brew install gifsicle` gives noticeably better results")
    args.out.mkdir(parents=True, exist_ok=True)
    args.cache.mkdir(parents=True, exist_ok=True)
    failures = []
    for n, e in enumerate(todo, 1):
        name = f"{args.prefix}{e.slug}"
        cached = args.cache / f"{e.codepoint}.gif"
        try:
            if not cached.exists():
                cached.write_bytes(fetch(GIF_URL.format(cp=e.codepoint)))
            data, size, step, ok = shrink(cached.read_bytes(), args.max_bytes, args.min_size, args.lossy)
        except Exception as ex:  # noqa: BLE001
            print(f"[{n}/{len(todo)}] {name}: ERROR {ex}")
            failures.append(name)
            continue
        dest = args.out / (f"{name}.gif" if ok else f"_TOO_BIG_{name}.gif")
        dest.write_bytes(data)
        flag = "" if ok else "  <-- OVER LIMIT"
        print(f"[{n}/{len(todo)}] {name}: {size}px, every {step} frame(s), {len(data) / 1024:.1f}KB{flag}")
        if not ok:
            failures.append(name)

    print(f"\ndone: {len(todo) - len(failures)} ok, {len(failures)} failed -> {args.out}/")
    if failures:
        print("failed:", ", ".join(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
