#!/usr/bin/env python3
"""Probe the new player core (f + P) end to end: the 5 shapes and the !set path, through a
written and re-read document."""

import sys

sys.path.insert(0, ".venv/lib/python3.14/site-packages")

from unitracker_player import PlayError, compile_document, play, read


def doc_text(monitor, phrase, steps):
    return (
        "unitracker: 1.20\n"
        "meta: {title: T, author: X}\n"
        "tempo: 1\n"
        "init: [-, -, -]\n"
        "song:\n"
        "  - def:\n"
        f"      ins:\n"
        f"        i1: {{mod: {monitor}}}\n"
        "      phrase:\n"
        f"        p1: [{phrase}]\n"
        f"        p2: [off*{steps}]\n"
        "    play: [p1, p2, p2]\n"
        "  - end\n"
    )


def words(monitor, phrase, steps):
    frames = play(
        compile_document(read(doc_text(monitor, phrase, steps))), ticks=steps, progress=False
    )
    return [(int(frames[t, 1]) << 8) | int(frames[t, 0]) for t in range(steps)]


def check(label, monitor, phrase, native):
    """Play the document and compare the frequency deltas against the native model's deltas,
    base-word independent."""
    got = words(monitor, phrase, len(native))
    base = got[0] - native[0]
    delta = [w - base for w in got]
    ok = delta == list(native)
    print(f"[{'OK' if ok else 'FAIL'}] {label}")
    if not ok:
        for t in range(len(native)):
            print(f"   t={t}  native={native[t]:6d}  doc={delta[t]:6d}")
    return ok


def detune_rows(steps):
    """The three voices' frequency low bytes, one shared instrument's ``detune`` applied by
    the voice slot."""
    text = (
        "unitracker: 1.20\n"
        "meta: {title: T, author: X}\n"
        "tempo: 1\n"
        "init: [-, -, -]\n"
        "song:\n"
        "  - def:\n"
        "      ins:\n"
        "        i1: {mod: set {detune: 1}}\n"
        "      phrase:\n"
        f"        p1: [A4.i1*{steps}]\n"
        "    play: [p1, p1, p1]\n"
        "  - end\n"
    )
    frames = play(compile_document(read(text)), ticks=steps, progress=False)
    return [[int(frames[t, 7 * v]) for v in range(3)] for t in range(steps)]


def native_slew(start, amp, ticks):
    """The tick-porta native: the offset's decay, ``amp`` plus one per tick, landing on 0."""
    out, s = [], start
    for _ in range(ticks):
        out.append(s)
        if s > 0:
            s = 0 if s <= amp else s - (amp + 1)
        elif s < 0:
            s = 0 if s + amp > 0 else s + (amp + 1)
    return out


def main():
    ok = True
    # The 5 shapes, each as an instrument's modulator over its bag.

    native_tri, out, vib = [], 0, 0
    vcmp = 64
    for _ in range(200):
        if 0x80 > vib > vcmp:
            vib ^= 0xFF
        vib = (vib + 2) & 0xFF
        out += 32 if not (vib & 1) else -32
        native_tri.append(out)
    ok &= check("triangle", "triangle {step: 32, extent: 64}", "A4.i1*200", native_tri)

    ok &= check("ramp+", "ramp {step: 32, delay: 2}", "A4.i1*5", [0, 0, 0, 32, 64])

    ok &= check("ramp-", "ramp {step: -24}", "A4.i1*4", [0, -24, -48, -72])

    ok &= check(
        "slew_to_target (the SID-Wizard carry)",
        "slew_to_target {target: 1, step: 1, delay: 3}",
        "A4.i1*6",
        [0, 0, 0, 0, 1, 1],
    )

    ok &= check(
        "slew_to_zero (tick porta)",
        "slew_to_zero {start: 160, amp: 32}",
        "A4.i1*6",
        native_slew(160, 32, 6),
    )

    # One shared instrument detunes by the voice slot: voice k plays the note plus k.
    rows = detune_rows(4)
    okd = all(r[1] == (r[0] + 1) & 0xFF and r[2] == (r[0] + 2) & 0xFF for r in rows)
    print(
        f"[{'OK' if okd else 'FAIL'}] a shared instrument's set {{detune: 1}} detunes by the voice slot"
    )
    ok &= okd

    # A !set event writes the bag's parameters; the next note re-arms from the instrument's bag.
    ok &= check(
        "!set step on a new note",
        "ramp {step: 32, delay: 3}",
        "A4.i1*5, A4.i1!set step 8*8",
        [0, 0, 0, 0, 32, 0, 0, 0, 0, 8, 16, 24, 32],
    )

    # An unknown shape name is refused at compile.
    try:
        words("sweep {amp: 1}", "A4.i1", 1)
        print("[FAIL] an unknown modulator is refused")
        ok = False
    except PlayError as exc:
        print(f"[OK] an unknown modulator is refused ({exc})")

    print("\nALL SHAPES + !set MATCH THE NATIVE MODELS:", ok)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
