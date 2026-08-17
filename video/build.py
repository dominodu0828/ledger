"""Render the demo video from captured evidence.

Every figure and every quoted answer is read from evidence.json, which is
written by capture_evidence.py against the live system — so the video cannot
drift from what the code actually does. Change the behaviour, re-capture,
rebuild, and the video is correct again.

    python video/capture_evidence.py
    python video/build.py            -> video/ledger-demo.mp4

Narration is synthesised with edge-tts, and every card's on-screen duration is
derived from the length of its spoken line rather than guessed, so audio and
video cannot drift apart.

Visual language follows the apple-design notes: an off-white ground with white
surfaces for depth rather than borders, near-black ink instead of pure black,
size-specific tracking (tight on display type, open on small caps), and leading
that runs inversely to size.
"""

import json
import pathlib
import subprocess
import sys

from PIL import Image, ImageDraw, ImageFont

HERE = pathlib.Path(__file__).resolve().parent
EV = json.loads((HERE / "evidence.json").read_text(encoding="utf-8"))
FRAMES = HERE / "frames"
OUT = HERE / "ledger-demo.mp4"

W, H = 1920, 1080
FPS = 30

BG       = (245, 245, 247)
SURFACE  = (255, 255, 255)
INK      = (29, 29, 31)
INK2     = (110, 110, 115)
HAIRLINE = (222, 222, 227)
PURPLE   = (88, 30, 232)
ORANGE   = (170, 95, 0)
RED      = (193, 32, 42)
GREEN    = (26, 127, 55)
TINT_RED = (253, 242, 242)
TINT_GRN = (241, 250, 243)

FD = pathlib.Path("C:/Windows/Fonts")
def sans(sz, bold=False):  return ImageFont.truetype(str(FD / ("segoeuib.ttf" if bold else "segoeui.ttf")), sz)
def semi(sz):              return ImageFont.truetype(str(FD / "seguisb.ttf"), sz)
def mono(sz, bold=False):  return ImageFont.truetype(str(FD / ("consolab.ttf" if bold else "consola.ttf")), sz)

CAPTION_Y = 928

VOICE = "en-US-AndrewNeural"
RATE = "+8%"
PRE, POST = 0.35, 0.75
REVEAL = 1.15
CAP = 176


def track_for(size: int) -> float:
    """Size-specific tracking: display type tightens, small type opens up.

    A single letter-spacing value is wrong somewhere — large text reads too
    loose at 0 and small text reads too tight.
    """
    if size >= 90:
        return -0.022 * size
    if size >= 46:
        return -0.012 * size
    if size <= 27:
        return 0.035 * size
    return 0.0


def draw_text(d, xy, s, font, fill, anchor=None, track=None):
    """Text with optional tracking. PIL has no letter-spacing, so step glyphs."""
    t = track_for(font.size) if track is None else track
    if abs(t) < 0.15:
        d.text(xy, s, font=font, fill=fill, anchor=anchor)
        return
    width = sum(d.textlength(c, font=font) + t for c in s) - t
    x, y = xy
    if anchor and anchor[0] == "m":
        x -= width / 2
    if anchor and len(anchor) > 1 and anchor[1] == "m":
        y -= font.size * 0.62
    for c in s:
        d.text((x, y), c, font=font, fill=fill)
        x += d.textlength(c, font=font) + t


def text_w(d, s, font, track=None):
    t = track_for(font.size) if track is None else track
    return sum(d.textlength(c, font=font) + t for c in s) - t


def clean_model_text(s: str) -> list[str]:
    """Flatten a model answer into display lines.

    Nova replies in markdown. Rendering `**bold**` and inline `1.` / `- `
    markers literally is the difference between a slide that reads like a
    product and one that reads like a debug dump.
    """
    s = s.replace("**", "").replace("\r", "")
    for marker in ("- ", "1. ", "2. ", "3. ", "4. "):
        s = s.replace(" " + marker, "\n" + marker)
    return [" ".join(l.split()) for l in s.split("\n") if l.strip()]


def wrap(text, font, maxw, draw):
    out, line = [], ""
    for word in text.split():
        trial = (line + " " + word).strip()
        if text_w(draw, trial, font) <= maxw:
            line = trial
        else:
            if line:
                out.append(line)
            line = word
    if line:
        out.append(line)
    return out


def base(label=None, caption=None):
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    if label:
        draw_text(d, (112, 76), label.upper(), semi(25), INK2)
    if caption:
        f = sans(36)
        for i, ln in enumerate(wrap(caption, f, W - 224, d)[:2]):
            draw_text(d, (112, CAPTION_Y + i * 50), ln, f, INK2)
    return img, d


def surface(d, box, radius=22, fill=SURFACE, edge=None):
    """A white card on the off-white ground: depth from material, not borders."""
    d.rounded_rectangle(box, radius=radius, fill=fill)
    if edge:
        d.rounded_rectangle(box, radius=radius, outline=edge, width=3)


def progress(img, frac):
    d = ImageDraw.Draw(img)
    d.rectangle([0, H - 6, W, H], fill=HAIRLINE)
    d.rectangle([0, H - 6, int(W * frac), H], fill=PURPLE)
    return img


# --------------------------------------------------------------------- cards

def card_title():
    img, d = base()
    draw_text(d, (W // 2, 396), "Ledger", sans(158, True), INK, anchor="mm")
    draw_text(d, (W // 2, 512), "Provenance-tracked, revocable agent memory", sans(48), INK2, anchor="mm")
    d.rounded_rectangle([W // 2 - 210, 576, W // 2 + 210, 582], radius=3, fill=PURPLE)
    draw_text(d, (W // 2, 648), "CockroachDB  ×  AWS Bedrock", semi(36), PURPLE, anchor="mm")
    return img


def card_statement(lines, label, caption, accent=INK):
    img, d = base(label, caption)
    f = sans(72, True)
    y = 392 - (len(lines) - 1) * 56
    for i, ln in enumerate(lines):
        draw_text(d, (112, y + i * 112), ln, f, accent if i == len(lines) - 1 else INK)
    return img


def card_document(reveal=4):
    img, d = base("01 · the input",
                  "An ordinary supplier document. The last two lines are the attack — "
                  "written to read like operations, not like an exploit.")
    draw_text(d, (112, 168), "acme-q4-update.pdf", semi(40), INK)
    draw_text(d, (112, 224), "uploaded · trust tier 0 · untrusted", mono(27), INK2)

    y, fm = 306, mono(29)
    for i, v in enumerate(EV["screening_verdicts"][:reveal]):
        attack = i >= 2
        surface(d, [112, y, W - 112, y + 116], fill=TINT_RED if attack else SURFACE)
        d.rounded_rectangle([112, y, 122, y + 116], radius=5, fill=RED if attack else HAIRLINE)
        for j, seg in enumerate(wrap(v["content"], fm, W - 320, d)[:2]):
            draw_text(d, (156, y + 26 + j * 40), seg, fm, RED if attack else INK)
        y += 140
    return img


def card_answer(text, label, caption, accent, badge, tint):
    img, d = base(label, caption)
    draw_text(d, (112, 172), badge, semi(34), accent)

    f = sans(42)
    lines = []
    for para in clean_model_text(text):
        bullet = para.startswith("- ") or (para[:1].isdigit() and para[1:3] == ". ")
        body = para.lstrip("-0123456789. ") if bullet else para
        for j, c in enumerate(wrap(body, f, W - 400, d)):
            lines.append((bullet and j == 0, c))
    lines = lines[:9]

    box_h = len(lines) * 54 + 76
    surface(d, [112, 232, W - 112, 232 + box_h], fill=tint)
    d.rounded_rectangle([112, 232, 122, 232 + box_h], radius=5, fill=accent)
    for i, (bullet, ln) in enumerate(lines):
        y = 270 + i * 54
        if bullet:
            d.ellipse([170, y + 18, 182, y + 30], fill=accent)
        draw_text(d, (206 if bullet else 170, y), ln, f, INK)
    return img


def card_verdicts(reveal=4):
    img, d = base("03 · the gate",
                  "Same document, screening on. Two lines refused — and refused inside the "
                  "write transaction, so they were never retrievable.")
    y, fm = 190, mono(27)
    for v in EV["screening_verdicts"][:reveal]:
        ok = v["admitted"]
        col = GREEN if ok else RED
        surface(d, [112, y, W - 112, y + 152], fill=TINT_GRN if ok else TINT_RED)
        d.rounded_rectangle([112, y, 122, y + 152], radius=5, fill=col)
        draw_text(d, (156, y + 26), "ADMIT" if ok else "REJECT", mono(30, True), col)
        draw_text(d, (156, y + 76), f"score {v['score']:.2f}", mono(26), INK2)
        for j, seg in enumerate(wrap(v["content"], fm, W - 620, d)[:2]):
            draw_text(d, (420, y + 26 + j * 36), seg, fm, INK)
        if not ok:
            draw_text(d, (420, y + 104), " · ".join(v["rules"]), mono(25), ORANGE)
        y += 176
    draw_text(d, (W - 112, 190), "gate threshold 0.50", mono(25), INK2, anchor="ra")
    return img


def card_cascade(stage=3):
    img, d = base("05 · containment",
                  "One transaction. A recursive walk over the derivation graph took the "
                  "agent's own note with it — a different source entirely.")
    surface(d, [140, 232, 880, 556])
    draw_text(d, (510, 300), "the vendor document held", sans(32), INK2, anchor="mm")
    draw_text(d, (510, 412), str(EV["source_memory_count"]), sans(148, True), INK, anchor="mm")
    draw_text(d, (510, 508), "live memories", sans(30), INK2, anchor="mm")

    if stage >= 2:
        surface(d, [1040, 232, 1780, 556], fill=TINT_RED)
        draw_text(d, (1410, 300), "revoking it killed", sans(32), INK2, anchor="mm")
        draw_text(d, (1410, 412), str(EV["revoked_count"]), sans(148, True), RED, anchor="mm")
        draw_text(d, (1410, 508), "memories", sans(30), INK2, anchor="mm")
        draw_text(d, (960, 392), "→", sans(64), INK2, anchor="mm")

    if stage >= 3:
        draw_text(d, (W // 2, 648), "Five, from a source that held four.", sans(54, True), INK, anchor="mm")
        draw_text(d, (W // 2, 722),
                  "The extra one is the note the agent wrote itself, on another source.",
                  sans(34), PURPLE, anchor="mm")
    return img


def card_timetravel():
    img, d = base("06 · audit",
                  "AS OF SYSTEM TIME reconstructs the belief state at an instant, with no "
                  "application-level versioning.")
    surface(d, [112, 176, W - 112, 356])
    draw_text(d, (156, 212), "SELECT ... FROM memories m JOIN sources s ON s.id = m.source_id", mono(29), INK2)
    draw_text(d, (156, 258), "AS OF SYSTEM TIME '2026-08-18 ...'", mono(29, True), PURPLE)
    draw_text(d, (156, 304), "WHERE m.revoked_at IS NULL", mono(29), INK2)

    for i, (n, lbl, col) in enumerate([
        (len(EV["replay_hits"]), "recalled before revocation", PURPLE),
        (len(EV["live_hits"]), "recalled now", INK2),
    ]):
        x = 430 + i * 1060
        draw_text(d, (x, 540), str(n), sans(138, True), col, anchor="mm")
        draw_text(d, (x, 648), lbl, sans(32), INK2, anchor="mm")
    draw_text(d, (W // 2, 540), "vs", sans(44), INK2, anchor="mm")
    return img


def card_architecture():
    img, d = base("07 · how", "Every guarantee is enforced by the database, not by the model.")
    rows = [
        ("screen()", "deterministic, trust-tier weighted; operator-only signals forfeit the discount", INK),
        ("Bedrock · Titan V2", "1024-dim normalized embeddings — before the transaction opens", ORANGE),
        ("ONE TRANSACTION", "memories + memory_edges + quarantine + audit_log, together or not at all", PURPLE),
        ("vector index + predicate", "similarity and 'allowed to influence this' in one query", INK),
        ("recursive CTE", "revocation cascades through the derivation graph", INK),
        ("AS OF SYSTEM TIME", "belief-state replay for the post-incident review", INK),
    ]
    y = 176
    for name, desc, col in rows:
        surface(d, [112, y, W - 112, y + 102])
        d.rounded_rectangle([112, y, 122, y + 102], radius=5, fill=col)
        draw_text(d, (156, y + 14), name, semi(36), col)
        draw_text(d, (156, y + 58), desc, sans(27), INK2)
        y += 118
    return img


def card_components():
    img, d = base("07 · how", "Three CockroachDB capabilities, two AWS services, one MCP server.")
    cols = [
        ("CockroachDB", PURPLE, ["Distributed Vector Indexing", "Serializable transactions",
                                 "Recursive CTE", "AS OF SYSTEM TIME", "ccloud CLI"]),
        ("AWS", ORANGE, ["Bedrock · Titan Embeddings V2", "Bedrock · Amazon Nova Pro",
                         "IAM · scoped Bedrock access"]),
        ("Surfaces", INK, ["MCP server · 8 tools", "FastAPI demo UI", "Public demo URL"]),
    ]
    for i, (title, col, items) in enumerate(cols):
        x = 112 + i * 576
        surface(d, [x, 190, x + 520, 800])
        draw_text(d, (x + 40, 232), title, sans(42, True), col)
        d.rounded_rectangle([x + 40, 304, x + 480, 308], radius=2, fill=col)
        for j, it in enumerate(items):
            draw_text(d, (x + 40, 348 + j * 66), it, sans(30), INK)
    return img


def card_end():
    img, d = base()
    draw_text(d, (W // 2, 392), "Ledger", sans(118, True), INK, anchor="mm")
    draw_text(d, (W // 2, 500), "memory that refuses to be poisoned", sans(46), PURPLE, anchor="mm")
    draw_text(d, (W // 2, 628), "github.com/dominodu0828/ledger", mono(32), INK2, anchor="mm")
    draw_text(d, (W // 2, 682), "ledger-0k5o.onrender.com", mono(32), INK2, anchor="mm")
    return img


# ------------------------------------------------------------------ timeline

def timeline():
    """Segments: the frames to show, and the line spoken over them."""
    return [
        dict(frames=[card_title()],
             say="Ledger. Provenance-tracked, revocable agent memory, "
                 "built on CockroachDB and AWS Bedrock."),

        dict(frames=[card_statement(
                ["An AI agent's memory is", "a persistent attack surface."],
                "00 · the problem",
                "Anything an attacker plants in a document the agent reads becomes a "
                "durable belief — recalled and acted on in every future session.")],
             say="An AI agent's memory is a persistent attack surface. When an agent reads a "
                 "document and writes what it learned into long-term memory, anything an "
                 "attacker planted there becomes a durable belief, recalled and acted on in "
                 "every future session."),

        dict(frames=[card_statement(
                ["Poison it once,", "and it stays poisoned."],
                "00 · the problem",
                "A filter in front of the store does not close this. It leaves a window "
                "where the poisoned row exists and is retrievable.", RED)],
             say="Poison it once, and it stays poisoned. A filter in front of the memory store "
                 "doesn't close this, because a filter that runs as a separate step from the "
                 "write leaves a window where the poisoned row exists and is retrievable."),

        dict(frames=[card_document(2), card_document(3), card_document(4)],
             say="Here is an ordinary supplier document. Four lines. The first two are real "
                 "operational facts. The last two are the attack, and notice they are written "
                 "to read like operations, not like an exploit."),

        dict(frames=[card_answer(
                EV["answer_poisoned"], "02 · without ledger",
                "No screening, no provenance. This is what an ordinary agent does with a "
                "document it was merely asked to read.",
                RED, "Q: How should I handle a refund request?", TINT_RED)],
             say="With no screening and no provenance, the agent stores all four. Now ask it a "
                 "completely normal question. It repeats the attacker's rule back, with the "
                 "account number, as if it were company policy. It was never told to do that "
                 "in this conversation. That is simply what it now believes."),

        dict(frames=[card_verdicts(2), card_verdicts(3), card_verdicts(4)],
             say="Same document, screening on. The two benign lines are admitted. The two "
                 "attack lines are refused, and the gate says exactly why. One voids prior "
                 "instructions; the other redirects money. Both are things only an operator is "
                 "ever allowed to say, so arriving from an uploaded document they get no "
                 "benefit of the doubt at all."),

        dict(frames=[card_answer(
                EV["answer_protected"], "04 · with ledger",
                "Not because the model resisted the instruction — because the instruction "
                "was never in the retrieval set to begin with.",
                GREEN, "Q: How should I handle a refund request?", TINT_GRN)],
             say="Same question, and now the correct answer, citing the operator channel. Not "
                 "because the model resisted the instruction, but because the instruction was "
                 "never in the retrieval set. Screening, embedding, the row and the audit "
                 "record are one CockroachDB transaction."),

        dict(frames=[card_cascade(1), card_cascade(2), card_cascade(3)],
             say="Now the harder problem. In the unprotected run the agent also wrote its own "
                 "note, on a different source. Revoking the vendor document revoked five "
                 "memories from a source that held four. A recursive walk over the derivation "
                 "graph took the agent's own conclusion with it."),

        dict(frames=[card_timetravel()],
             say="And for the post-incident review, AS OF SYSTEM TIME replays exactly what the "
                 "agent believed before the revocation, with no versioning code of our own."),

        dict(frames=[card_architecture()],
             say="Every guarantee here is enforced by the database rather than the model: "
                 "distributed vector indexing, serializable transactions, a recursive CTE, "
                 "and time travel."),

        dict(frames=[card_components()],
             say="AWS Bedrock supplies Titan embeddings and Nova Pro for reasoning. Ledger "
                 "also ships as an MCP server, so any agent runtime can mount it."),

        dict(frames=[card_end()],
             say="Ledger. Memory that refuses to be poisoned."),
    ]


# --------------------------------------------------------------------- build

def ffprobe_duration(path) -> float:
    return float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True).stdout.strip())


def narrate(segments, audio_dir):
    import asyncio
    import edge_tts

    audio_dir.mkdir(exist_ok=True)

    async def all_of():
        await asyncio.gather(*[
            edge_tts.Communicate(seg["say"], VOICE, rate=RATE).save(str(audio_dir / f"{i:03d}.mp3"))
            for i, seg in enumerate(segments)
        ])

    asyncio.run(all_of())
    return [ffprobe_duration(audio_dir / f"{i:03d}.mp3") for i in range(len(segments))]


def main():
    FRAMES.mkdir(exist_ok=True)
    for old in list(FRAMES.glob("*.png")) + list(FRAMES.glob("*.txt")):
        old.unlink()
    audio_dir = HERE / "audio"

    segments = timeline()
    print("synthesising narration...")
    speech = narrate(segments, audio_dir)

    total = sum(PRE + s + POST for s in speech)
    print(f"segments: {len(segments)}   total: {total:.1f}s "
          f"({int(total) // 60}:{int(total) % 60:02d})")
    if total > CAP:
        sys.exit(f"OVER by {total - CAP:.1f}s — shorten the `say` lines or raise RATE")

    vlist, alist, elapsed = [], [], 0.0
    for i, (seg, spoken) in enumerate(zip(segments, speech)):
        seg_total = PRE + spoken + POST
        frames = seg["frames"]
        holds = [REVEAL] * (len(frames) - 1)
        holds.append(max(1.0, seg_total - sum(holds)))
        for j, (img, hold) in enumerate(zip(frames, holds)):
            progress(img, elapsed / total)
            p = FRAMES / f"{i:03d}_{j}.png"
            img.save(p)
            vlist.append(f"file '{p.as_posix()}'\nduration {hold:.3f}")
            elapsed += hold

        padded = audio_dir / f"pad_{i:03d}.wav"
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", str(audio_dir / f"{i:03d}.mp3"),
             "-af", f"adelay={int(PRE*1000)}|{int(PRE*1000)},apad=whole_dur={seg_total:.3f}",
             "-ar", "48000", "-ac", "2", str(padded)],
            check=True)
        alist.append(f"file '{padded.as_posix()}'")

    vlist.append(vlist[-1].split("\nduration")[0])
    (FRAMES / "video.txt").write_text("\n".join(vlist), encoding="utf-8")
    (FRAMES / "audio.txt").write_text("\n".join(alist), encoding="utf-8")

    print("encoding...")
    r = subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "concat", "-safe", "0", "-i", str(FRAMES / "video.txt"),
         "-f", "concat", "-safe", "0", "-i", str(FRAMES / "audio.txt"),
         "-vf", f"fps={FPS},format=yuv420p",
         "-c:v", "libx264", "-preset", "medium", "-crf", "20",
         "-c:a", "aac", "-b:a", "160k", "-shortest",
         "-movflags", "+faststart", str(OUT)],
        capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr[-3000:])
        sys.exit("ffmpeg failed")
    print(f"wrote {OUT}  ({OUT.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
