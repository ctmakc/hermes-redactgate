"""Cut the raw screen recording into the ≤90 s demo: title card, trimmed segments, captions,
end card. Every frame of the terminal part is the real recording; edits are cuts (Hermes
start-up wait) and one labelled speed-up (typing the prompt, 3×).

    python demo/compose.py <rec_dir> <out.mp4>
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H, BAND = 1280, 720, 80
SANS = "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf"
SANS_B = "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf"
MONO = "/home/llm/.local/share/fonts/adi1090x/JetBrainsMono/JetBrainsMono-Regular.ttf"
CARBON, VELLUM, CYAN, VERMILLION, MUTED = "#0f1115", "#f1ece1", "#3cc6d8", "#e4572e", "#9aa3ad"

rec = Path(sys.argv[1])
out = Path(sys.argv[2])
work = rec / "compose"
work.mkdir(exist_ok=True)
raw = rec / "raw.mp4"
t0 = float((rec / "ffmpeg_t0").read_text())
marks = {m["mark"]: m["t"] - t0 for m in json.loads((rec / "marks.json").read_text())["marks"]}


def run(*args: str) -> None:
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *args], check=True)


# ── segments of the raw recording (seconds) ─────────────────────────────────
ready = marks["status"] - 1.5          # Hermes prompt on screen
seg = [
    ("a", marks["start"] - 0.5, marks["launch"] + 0.9, 1.0),   # type `hermes`
    ("b", ready - 0.1, marks["typing"], 1.0),                 # /redact, /redact harden
    ("c", marks["typing"], marks["sent"] + 0.3, 3.0),         # typing the prompt, 3x
    ("d", marks["sent"] + 0.3, marks["last"], 1.0),           # model works, real time
    ("e", marks["last"], marks["end"] + 0.4, 1.0),            # /redact last, audit, verify, cat
]
parts = []
for name, a, b, speed in seg:
    p = work / f"{name}.mp4"
    vf = f"crop={W}:{H - BAND}:0:0,pad={W}:{H}:0:0:color={CARBON}" + (f",setpts=PTS/{speed}" if speed != 1 else "")
    run("-ss", f"{a:.2f}", "-to", f"{b:.2f}", "-i", str(raw), "-vf", vf, "-r", "30", "-an",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", str(p))
    parts.append((name, (b - a) / speed))


def font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def card(path: Path, lines: list[tuple[str, str, int, str]], bar: bool = True) -> None:
    img = Image.new("RGB", (W, H), CARBON)
    d = ImageDraw.Draw(img)
    y = 210
    for text, face, size, color in lines:
        f = font(face, size)
        d.text((110, y), text, font=f, fill=color)
        y += int(size * 1.55)
    if bar:  # the redaction bar: RedactGate's mark
        d.rectangle([110, 160, 330, 182], fill=VELLUM)
        d.rectangle([340, 160, 420, 182], fill=CYAN)
    img.save(path)


title = work / "title.png"
card(title, [
    ("RedactGate for Hermes Agent", SANS_B, 54, VELLUM),
    ("Your agent works with real client data.", SANS, 30, VELLUM),
    ("The model only ever sees placeholders.", SANS, 30, CYAN),
    ("Real screen recording · Hermes v0.23 · Muse Spark via api.meta.ai", SANS, 20, MUTED),
])
end = work / "end.png"
card(end, [
    ("hermes plugins install ctmakc/hermes-redactgate", MONO, 30, VELLUM),
    ("github.com/ctmakc/hermes-redactgate", SANS, 28, CYAN),
    ("Offline · standard library only · hash-chained audit · AGPL-3.0", SANS, 22, MUTED),
    ("Built for the Nous Research Hermes Agent Plugin Hackathon", SANS, 22, MUTED),
])
TITLE_S, END_S = 3.0, 4.5
for name, img, dur in (("title", title, TITLE_S), ("end", end, END_S)):
    run("-loop", "1", "-t", str(dur), "-i", str(img), "-vf", "fps=30,format=yuv420p", "-c:v", "libx264",
        "-preset", "veryfast", "-crf", "18", str(work / f"{name}.mp4"))

order = ["title", *[n for n, _ in parts], "end"]
(work / "list.txt").write_text("".join(f"file '{work / n}.mp4'\n" for n in order))
cut = work / "cut.mp4"
run("-f", "concat", "-safe", "0", "-i", str(work / "list.txt"), "-c", "copy", str(cut))

# ── captions on the edited timeline ─────────────────────────────────────────
start = {}
t = TITLE_S
for name, dur in parts:
    start[name] = t
    t += dur
total = t + END_S


def at(raw_t: float, name: str) -> float:
    a = dict((n, (x, sp)) for n, x, _, sp in seg)[name]
    return start[name] + (raw_t - a[0]) / a[1]


diff_shown = marks["sent"] + 0.3 + 0.82 * (marks["last"] - marks["sent"])  # when the write_file diff appeared in this take
caps = [
    (start["a"], start["b"], "Hermes Agent with the RedactGate plugin installed"),
    (start["b"], at(marks["harden"], "b"), "/redact: what is protected, plus two Hermes side channels no plugin can rewrite"),
    (at(marks["harden"], "b"), start["c"], "/redact harden closes the one that fires every session: model-made titles"),
    (start["c"], start["d"], "A request with a client's name, e-mail, phone and card   (typing shown 3× faster)"),
    (start["d"], at(diff_shown, "d"), "Muse Spark on api.meta.ai receives [[PERSON_…]], [[EMAIL_…]] … nothing else"),
    (at(diff_shown, "d"), start["e"], "write_file is a local tool, so RedactGate hands it the real values back"),
    (start["e"], at(marks["audit"], "e"), "/redact last: your message exactly as api.meta.ai received it"),
    (at(marks["audit"], "e"), at(marks["verify"], "e"), "Hash-chained audit: entity counts, never values. No side-channel call this time"),
    (at(marks["verify"], "e"), at(marks["exit"], "e"), "/redact verify: an edited or deleted line breaks the chain"),
    (at(marks["exit"], "e"), total - END_S, "On disk: the real name and e-mail, written by the local tool"),
]
inputs, filters, prev = [], [], "0:v"
for i, (a, b, text) in enumerate(caps):
    img = Image.new("RGBA", (W, BAND), CARBON)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 2], fill="#262b33")
    d.rectangle([40, 28, 48, 52], fill=CYAN)
    d.text((64, 22), text, font=font(SANS, 26), fill=VELLUM)
    p = work / f"cap{i}.png"
    img.save(p)
    inputs += ["-i", str(p)]
    filters.append(f"[{prev}][{i + 1}:v]overlay=0:{H - BAND}:enable='between(t,{a:.2f},{b - 0.01:.2f})'[v{i}]")
    prev = f"v{i}"
run("-i", str(cut), *inputs, "-filter_complex", ";".join(filters), "-map", f"[{prev}]", "-r", "30",
    "-c:v", "libx264", "-preset", "slow", "-crf", "20", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out))
print(f"{out}  {total:.1f}s")
for a, b, text in caps:
    print(f"  {a:5.1f}–{b:5.1f}  {text}")
