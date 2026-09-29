# noto-emoji-grabber

Downloads [Noto animated emoji](https://googlefonts.github.io/noto-emoji-animation/) and converts them into GIFs small enough to upload as Slack custom emoji. Emoji already in your Slack are skipped.

## Requirements

- [uv](https://docs.astral.sh/uv/). The script declares its own dependency (Pillow) inline, so there's nothing else to install.
- Optional but recommended: [gifsicle](https://www.lcdf.org/gifsicle/) (`brew install gifsicle`). If it's installed, the script uses it automatically, which usually keeps emoji larger or with more frames.

## Usage

```sh
./grab.py --list                 # list emoji not yet in Slack
./grab.py                        # download + convert all missing emoji into out/
./grab.py --only star ogre       # (re)process specific emoji, ignoring the existing list
./grab.py --only pie --max-bytes 100000   # force a smaller file
```

Other options: `--existing`, `--out`, `--cache`, `--prefix` (default `noto-`), `--min-size` (default 64), `--lossy` (gifsicle level, default 40), `--limit`. Run `./grab.py --help` for details.

## How it works

1. Reads the emoji index from `https://googlefonts.github.io/noto-emoji-animation/data/api.json` (the data behind the search site). This isn't a documented API, so it may change.
2. Skips any emoji whose name appears as `:noto-<name>:` in `existing-noto-emoji.txt`. Names are converted to lowercase and hyphenated, e.g. `:Paw prints:` becomes `paw-prints`.
3. Downloads `https://fonts.gstatic.com/s/e/notoemoji/latest/<codepoint>/512.gif` into `.cache/`.
4. Shrinks each emoji until it's under `--max-bytes` (default 120,000 bytes, a little under Slack's 128KB limit):
   - scales down 128 → 112 → 96 → 80 → 64px
   - then, at the minimum size, keeps only every 2nd … 6th frame. Each kept frame is shown longer, so playback speed stays the same.
5. Writes `out/noto-<name>.gif`. Anything that can't fit is saved as `out/_TOO_BIG_noto-<name>.gif` and listed at the end.

## Uploading to Slack

The easiest way to bulk upload is the Neutral Face Emoji Tools Chrome extension: drag in everything in `out/` except `_TOO_BIG_*` files. It names each emoji after its filename. Check that your company allows browser extensions before installing it.

After uploading, add the new names to `existing-noto-emoji.txt` so the next run skips them.
