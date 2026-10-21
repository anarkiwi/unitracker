#!/usr/bin/env python3
"""Validate the new player core (f + P) against the prototype's 5 shapes."""

import sys

sys.path.insert(0, ".venv/lib/python3.14/site-packages")

from prototype import ramp, slew_to_target, slew_to_zero, triangle, pulse_sweep, play
from unitracker_player.player import play as vendored_play, compile_document
import unitracker_player.document as doc


def vendored(fn, P0, ticks, note=441):
    ins = doc.Instrument(fn=fn, P0=dict(P0))
    d = doc.Document(
        header=doc.Header(version="1.20", meta={"title": "T", "author": "X"}),
        tables={},
        init=(
            doc.Event("A4", "i1", (), False, False, 1),
            doc.Event("-", None, (), False, False, 1),
            doc.Event("-", None, (), False, False, 1),
        ),
        instruments={"i1": ins},
        programs={},
        expressions={},
        phrases={
            "p1": (doc.Event("A4", "i1", (), False, False, 1),),
            "p2": (doc.Event("-", None, (), False, False, 1),),
        },
        timeline=[
            [doc.Play("phrase", "p1", 0), doc.Play("phrase", "p2", 0), doc.Play("phrase", "p2", 0)],
        ],
        loop=None,
    )
    song = compile_document(d)
    frames = vendored_play(song, ticks=ticks, progress=False)
    # freq lives at voice index 0 in the 25-register row, as a 16-bit value at offset 0..1
    return [(int(frames[t, 1]) << 8) | int(frames[t, 0]) for t in range(len(frames))]


def check(label, fn, P0, native, note=441):
    proto = play(fn, P0, len(native), note=note)[: len(native)]
    okp = all(a == b for a, b in zip(native, proto))
    vended = vendored(fn, P0, len(native), note)
    shift = vended[0] - native[0]
    native = [x + shift for x in native]
    okv = all(a == b for a, b in zip(native, vended))
    tag = "OK" if (okp and okv) else "FAIL"
    print(f"[{tag}] {label}  proto={okp} vendored={okv}")
    if not okv:
        for t in range(min(len(native), 8)):
            print(f"   t={t}  native={native[t]:6d}  vendored={vended[t]:6d}")
    return okp and okv


if __name__ == "__main__":
    ok = True
    n = 441
    F16 = 0xFFFF

    native_tri, out, vib = [], 0, 0
    vcmp = 64
    for _ in range(10):
        if 0x80 > vib > vcmp:
            vib ^= 0xFF
        vib += 2
        out += 32 if not (vib & 1) else -32
        native_tri.append(n + (out & F16))
    ok &= check("goat triangle", triangle, {"note": n, "step": 32, "extent": vcmp}, native_tri)

    ok &= check(
        "goat ramp+",
        ramp,
        {"step": 32, "delay": 2, "mask": F16},
        [n, n] + [n + 32 * (t - 2) for t in range(2, 8)],
    )

    ok &= check("sidwizard ramp-", ramp, {"step": -24, "mask": F16}, [n - 24 * t for t in range(8)])

    native_p, voff = [], 160
    for _ in range(8):
        native_p.append(n + voff)
        voff = 0 if 0 < voff <= 32 else (voff - 33 if voff > 32 else voff)
    ok &= check(
        "sidwizard tick_porta", slew_to_zero, {"note": n, "amp": 32, "offset": 160}, native_p
    )

    native_a, voff = [], 200
    for _ in range(8):
        native_a.append(n + voff)
        voff = 0 if 0 < voff <= 64 else (voff - 65 if voff > 64 else voff)
    ok &= check("defmon ACID step", slew_to_zero, {"note": n, "amp": 64, "offset": 200}, native_a)

    print("\nALL SHAPES MATCH (prototype + vendored core):", ok)
    sys.exit(0 if ok else 1)
