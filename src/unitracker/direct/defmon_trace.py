"""A defMON replay traced to the format-neutral event stream (:mod:`unitracker.direct.trace`),
built by :mod:`unitracker.direct.builder`; the replay harness is :mod:`unitracker.direct.defmon`'s.

A position is a voice's two sidcall cascades, its gate bits and whether a note fixes the gate
off on its first call; the walk merges both layers' rows of a call (docs/direct/defmon.md).
"""

# pylint: disable=duplicate-code

import sys

from pydefmon import DefmonPlayer
from pysidtracker import SidImage
from unitracker_grammar import document as d

from unitracker.direct import builder, cli
from unitracker.direct import defmon as dm
from unitracker.direct.dmcode import CODE, STATE
from unitracker.direct.dmimage import LiftError, locate, row_fields
from unitracker.direct.trace import Command, Effect, Note, Start, Step

CASCADES = (0x12EF, 0x12E0, 0x1382, 0x1373)


class Tracer:  # pylint: disable=too-many-instance-attributes
    """One subtune of a defMON replay, a build of the ``$1000`` build's code, traced."""

    def __init__(self, raw: bytes, subtune: int = 0):
        self.raw, self.subtune = raw, subtune
        self.header = SidImage.from_bytes(raw).header
        self.player = DefmonPlayer(raw, subtune=subtune)
        self.mem = self.player._mem  # pylint: disable=protected-access
        try:
            self.at, variants = locate(self.mem)
        except LiftError as exc:
            raise dm.TranscribeError(str(exc)) from exc
        self.absolute = "absolute" in variants
        self.uncut, self.fixed, self.direct = (n in variants for n in ("uncut", "fixed", "direct"))
        dm.Transcriber.check(self)
        self.names, self.joints = {}, {}

    __call__ = dm.Transcriber.__call__
    # pylint: disable-next=protected-access
    _floor, _cutoff, word = dm.Transcriber._floor, dm.Transcriber._cutoff, dm.Transcriber.word
    resolve, sidtab, tick = dm.Transcriber.resolve, dm.Transcriber.sidtab, dm.Transcriber.tick

    def columns(self, eff: int) -> dict:
        """The columns of sidTAB row ``eff``'s body, in the walk's ($16B0) order."""
        return row_fields(
            self.mem, self.sidtab(dm.SIDTAB_LO, eff) | self.sidtab(dm.SIDTAB_HI, eff) << 8
        )

    def head(self) -> dict:
        """The header parts, the code templates and the one instrument."""
        return {
            "meta": tuple((k, dm.text(getattr(self.header, a))) for k, a in dm.META),
            "clock": (int(self.player.cycles_per_frame), dm.clock_hz(self.header)),
            "slots": dm.SLOTS,
            "state": tuple((cell, tuple(names.items())) for cell, names in STATE.items()),
            "tables": dm.Transcriber.tables(self),
            "code": {
                k: v if self.absolute else v.replace(dm.NOTE7, "adc #24\n") for k, v in CODE.items()
            },
            "instruments": {"i1": d.Instrument({"p.freq": dm.call("pitch")})},
        }

    def init(self) -> tuple:
        """Each voice's init event (see :meth:`unitracker.direct.defmon.Transcriber.init`)."""
        start = d.Run("ctrl", d.Program([{"note": dm.note_name(0)}]))
        return tuple(
            d.replace(e, commands=tuple((k, start if k == "run" else v) for k, v in e.commands))
            for e in dm.Transcriber.init(self)
        )

    def events(self):
        """The song's steps and events, each step keyed by its arranger position and every
        voice's cascades and gate bits; the pitch step bound at tick 1."""
        states = [tuple(self(a, v) for a in CASCADES) for v in range(3)]
        bits = [(0, 0)] * 3
        player = DefmonPlayer(self.raw, subtune=self.subtune)
        for k, step in enumerate(dm.sequence(player, self.at, dm.MAX_CALLS, self.fixed)):
            yield Step(step.call + 1, (step.key, tuple(states), tuple(bits)))
            if not k:
                yield from (Command(1, v, *b) for v in range(3) for b in dm.BINDS)
            for t in range(step.length):
                for v, marks in enumerate(step.events):
                    ev, tick = marks.get(t), step.call + 1 + t
                    states[v], bits[v], pos = self.call(v, states[v], bits[v], ev)
                    if ev is not None and ev[2] is not None:
                        if self.absolute and ev[2] >= 0x80:
                            raise dm.TranscribeError(f"note ${ev[2]:02X} past the note column")
                        yield Note(tick, v, dm.note_name(ev[2]))
                    if pos is not None:
                        yield Start(tick, v, pos)

    def call(self, voice: int, state: tuple, bits: tuple, ev: tuple | None) -> tuple:
        """``(state, bits, pos)``: a voice's cascades and gate bits after a call running pattern
        event ``ev`` (or None), and the position of the program the event starts, if any."""
        state = list(state)
        if ev is not None:
            state[0:2] = state[0:2] if ev[0] is None else (ev[0], 0)
            state[2:4] = state[2:4] if ev[1] is None else (ev[1], 0)
        after, cols, out, sets = *self.tick(tuple(state)), bits, False
        for c in dm.merged(cols) if cols else ():
            out, c = dm.gated(out, c)
            sets |= "_gate" in c
        if ev is None:
            return after, out, None
        fix = ev[2] is not None and not sets and not out[0] ^ out[1]
        run = ev[0] is not None or ev[1] is not None or fix
        return after, out, (voice, tuple(state), bits, fix) if run else None

    def step(self, pos: tuple) -> tuple:
        """One call of both layers from ``pos``: the row of the columns it fetches; a fixing
        note's first call writes the gate off where its row leaves the gate."""
        voice, state, bits, fix = pos
        if state[1] >= 0x80 and state[3] >= 0x80:
            return None, [self.row({"_gate": 0}, voice)] if fix else []
        after, cols = self.tick(state)
        rows = []
        for c in dm.merged(cols) if cols else [{}] if fix else []:
            bits, c = dm.gated(bits, c)
            rows.append(self.row({"_gate": 0} | c if fix else c, voice))
        return (voice, after, bits, False), rows

    def row(self, cols: dict, voice: int) -> tuple:
        """A call's sidTAB columns on ``voice`` as document keys and Effects."""
        old = dm.Transcriber.row(self, 1, cols, voice).keys
        keys = [(k, v) for k, v in old if not k.endswith((".pw", ".freq", ".cut"))]
        if {"pw", "ps", "af"} & cols.keys():
            flags = ("pw" in cols) | ("ps" in cols) << 1
            keys.append(
                Effect(
                    "pulse",
                    (("set", flags), ("width", cols.get("pw", 0)), ("speed", cols.get("ps", 0))),
                )
            )
        if dm.ACID & cols.keys():
            for x, (cp, col) in zip("ab", (("cp", "acid"), ("bcp", "bacid"))):
                g, c, j, k = dm.acid({"cp": cols.get(cp), "acid": cols.get(col)}).args
                keys.append(
                    Effect(
                        "cutoff",
                        (("layer", x), ("set", g), ("offset", c), ("value", j), ("step", k)),
                    )
                )
        if "af" in cols:
            keys.append(mode(cols["af"]))
        return tuple(keys)

    def pulse(self, keys: list) -> d.Run:
        """Layer B's filter ``keys`` as a one-row pulse-slot program."""
        return d.Run("pw", d.Program([d.Row(keys)]))

    @staticmethod
    def bind(effect: Effect, voice: int) -> tuple:
        """The defMON code template bindings of ``effect`` on ``voice``."""
        p = dict(effect.params)
        if effect.kind == "pulse":
            return (("sw.pw", dm.call("pul", g=p["set"], p=p["width"], h=p["speed"])),)
        if effect.kind == "cutoff":
            call = dm.call("acid", g=p["set"], c=p["offset"], j=p["value"], k=p["step"])
            return ((f"k{voice + 1}{p['layer']}.cut", call),)
        m = {"pitch": 0, "glide": 0x80 | p.get("down", 0) << 6 | p.get("rate", 0)}.get(effect.kind)
        m = p["interval"] if m is None else m
        return (("p.freq", dm.pitch(m)), ("pm.pw", dm.call("pmode", m=m)))


def mode(m: int) -> Effect:
    """The pitch step's effect in pitch mode ``m``."""
    if not m:
        return Effect("pitch")
    if m >= 0x80:
        return Effect("glide", (("rate", m & 0x3F), ("down", m >> 6 & 1)))
    return Effect("detune", (("interval", m),))


def transcribe(raw: bytes, subtune: int = 0) -> str:
    """The document of one subtune of a defMON SID, built from its trace."""
    return builder.build(Tracer(raw, subtune)).text


def verify(raw: bytes, subtune: int = 0, frames: int = 20000) -> tuple:
    """``(cells, document, traps)``: the built document's grid against the reference's."""
    return builder.verify(builder.build(Tracer(raw, subtune)), dm.reference, raw, subtune, frames)


def main(argv=None) -> int:
    """Transcribe a SID by its trace, or verify SIDs against pydefmon's replay."""
    return cli.main(argv, __doc__.split("\n", 1)[0], transcribe, verify)


if __name__ == "__main__":
    sys.exit(main())
