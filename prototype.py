#!/usr/bin/env python3
"""Modulator as a parameterised arithmetic function, generalized over goat/sidwizard/defmon.

P is a plain dict of parameters an event may write. Each shape is one arithmetic
expression; play re-evaluates it per tick against the current (P, v, t).
"""

F16, F12 = 0xFFFF, 0x0FFF


def set_note(P, v, t, note=0):
    return P.get("note", v)


def ramp(P, v, t, note=0):
    if t < P.get("delay", 0):
        return note
    step = P.get("step", 0)
    ticks = t - P.get("delay", 0)
    return (note + step * ticks + P.get("offset", 0)) & P.get("mask", F16)


def slew_to_target(P, v, t, note=0):
    if t < P.get("delay", 0):
        return note
    target = P.get("target", note)
    step = max(1, abs(P.get("step", 1)))
    out = note
    for _ in range(t - P.get("delay", 0)):
        if out == target:
            break
        d = target - out
        if abs(d) <= step:
            out = target
        else:
            out += step if d > 0 else -step
    return out & P.get("mask", F16)


def slew_to_zero(P, v, t, note=0):
    amp = P.get("amp", 0)
    step = max(1, amp + 1)
    signed = P.get("offset", 0)
    if signed > (1 << 15):
        signed -= 1 << 16
    for _ in range(max(0, t - P.get("delay", 0))):
        if signed == 0:
            break
        if signed > 0:
            signed = 0 if signed <= amp else signed - step
        else:
            signed = 0 if signed + amp > 0 else signed + step
    return (note + signed) & F16


def triangle(P, v, t, note=0):
    if t < P.get("delay", 0):
        return note
    amp = abs(P.get("step", 0)) or 1
    vcmp = P.get("extent", 128)
    vib, out = 0, 0
    for _ in range(t - P.get("delay", 0) + 1):
        if 0x80 > vib > vcmp:
            vib ^= 0xFF
        vib += 2
        out += -amp if vib & 1 else amp
    return (note + (out & F16)) & F16


def pulse_sweep(P, v, t, note=0):
    if t >= P.get("ticks", 0):
        return note
    out = note + P.get("step", 0) * t
    hi = P.get("max")
    if hi is not None:
        out = out if P.get("step", 0) < 0 else min(out, hi)
    return out & P.get("mask", F12)


def play(f, P, ticks, note=441, events=()):
    out, v = [], note
    table = {}
    for t in range(ticks):
        for et, k, val in events:
            if et <= t:
                table[k] = val
            else:
                break
        v = f({**P, **table}, v, t, note) & F16
        out.append(v)
    return out


if __name__ == "__main__":
    import sys

    def check(label, f, P, native, note=None):
        base = note if note is not None else P.get("note", 441)
        mine = play(f, P, len(native), note=base)
        ok = all(a == b for a, b in zip(native, mine))
        print(f"[{'OK' if ok else 'FAIL'}] {label}")
        if not ok:
            for t in range(min(len(native), 10)):
                print(f"   t={t}  native={native[t]:6d}  mine={mine[t]:6d}")
        return ok

    ok = True
    n = 441

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

    tgt = n + 160
    native_tp = [n] * 2
    c = n
    for _ in range(6):
        native_tp.append(c)
        c = tgt if c + 32 >= tgt else c + 32
    ok &= check(
        "goat toneporta",
        slew_to_target,
        {"note": n, "target": tgt, "step": 32, "delay": 2, "mask": F16},
        native_tp,
    )

    ok &= check("sidwizard ramp-", ramp, {"step": -24, "mask": F16}, [n - 24 * t for t in range(8)])

    native_p, voff = [], 160
    for _ in range(8):
        native_p.append(n + voff)
        voff = 0 if 0 < voff <= 32 else (voff - 33 if voff > 32 else voff)
    ok &= check(
        "sidwizard tick_porta", slew_to_zero, {"note": n, "amp": 32, "offset": 160}, native_p
    )

    ok &= check(
        "defmon pulse bounded ramp",
        pulse_sweep,
        {"note": 64, "step": 8, "ticks": 8, "max": 0xFFF, "mask": F12},
        [64 + 8 * i for i in range(8)],
    )

    native_a, voff = [], 200
    for _ in range(8):
        native_a.append(n + voff)
        voff = 0 if 0 < voff <= 64 else (voff - 65 if voff > 64 else voff)
    ok &= check("defmon ACID step", slew_to_zero, {"note": n, "amp": 64, "offset": 200}, native_a)

    print("\nALL SHAPES MATCH:", ok)
    sys.exit(0 if ok else 1)
