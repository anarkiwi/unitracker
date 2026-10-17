"""Envelope commands: ``!ad:n`` and ``!sr:n`` as the byte a player's nibble small FX writes.

A command code with ``nibble`` (its shift) and ``slot`` (0 the instrument column, 1 the FX column)
writes the sounding instrument's byte with that nibble replaced, and the other register again
from its override, which must leave it unchanged; overrides last until the next note start.
"""

import functools

from unitracker.codec import ENVELOPE, envelope_names
from unitracker.lifetime import order, starts
from unitracker.vm import event_commands

NIBBLE = 0x0F


class EnvelopeError(ValueError):
    """An envelope command the player can't play where it is written."""


class _Voice:
    """One voice's instrument, envelope registers and nibble overrides."""

    # pylint: disable=too-few-public-methods

    def __init__(self):
        self.ins, self.regs, self.over = 0, {}, {}


def _with(event: dict, commands) -> dict:
    """``event`` with ``commands`` as its commands."""
    out = {k: v for k, v in event.items() if k not in ("command", "commands")}
    if len(commands) == 1:
        out["command"] = list(commands[0])
    elif commands:
        out["commands"] = [list(c) for c in commands]
    return out


def _played(trk: dict, phrases: dict, convert) -> dict:
    """``{play: commands}``: each play's events' commands as ``convert(voice, event)`` gives
    them where the voice plays it, refusing a play that they differ on between its passes."""
    voice, seen = _Voice(), {}
    for play, name in order(trk):
        got = tuple(tuple(convert(voice, e)) for e in phrases[name])
        if seen.setdefault(play, got) != got:
            problem = f"a play of phrase {name} plays its envelope commands differently"
            raise EnvelopeError(f"{problem} on its passes")
    return seen


def _alias(variants: list, name: str, got: tuple) -> str:
    """The phrase that plays ``name`` as ``got``: ``name`` for its first variant."""
    if got not in variants:
        variants.append(got)
    k = variants.index(got)
    return name if k == 0 else f"{name}/{k}"


class Envelope:
    """A playroutine's envelope commands over a song's records of types ``rtypes``."""

    def __init__(self, pr: dict, rtypes: dict):
        inst = pr.get("instrument") or {}
        self.ins_type, self.base = inst.get("type"), int(inst.get("base", 0))
        self.rtype = rtypes.get(self.ins_type)
        self.legato = ((pr.get("modulators") or {}).get("legato") or {}).get("command")
        self.native = {}
        for code, variants in (pr.get("commands") or {}).items():
            for var in variants if isinstance(variants, list) else [variants]:
                if "nibble" in var:
                    spec = (str(var["name"]), int(var["nibble"]), int(var["slot"]))
                    self.native[int(code)] = spec
        self.codes = {v: c for c, v in self.native.items()}
        self.names = envelope_names(pr.get("commands"))

    def code(self, name: str) -> int:
        """The internal code of written command ``name``, its argument the byte."""
        return ENVELOPE - self.names.index(name)

    def name(self, code: int) -> str | None:
        """The command name of internal code ``code``, None for any other code."""
        k = ENVELOPE - code
        return self.names[k] if 0 <= k < len(self.names) else None

    @staticmethod
    def slot(event: dict, k: int) -> int:
        """The column of the ``k``-th command of ``event``: on an event that names an instrument,
        which fills the instrument column, the FX column."""
        return 1 if event.get("instrument") else k

    def _byte(self, records: dict, voice: _Voice, name: str) -> int:
        """Register ``name`` as the player writes it from the voice's instrument and override."""
        row = records.get(self.ins_type, {}).get(voice.ins, {})
        byte = int(row.get(name, self.rtype.default(name)))
        if name in voice.over:
            shift, nibble = voice.over[name]
            byte = byte & ~(NIBBLE << shift) | nibble << shift
        return byte

    def _enter(self, records: dict, voice: _Voice, event: dict) -> None:
        """Select the event's instrument, and start its note."""
        if event.get("instrument"):
            voice.ins = int(event["instrument"]) - self.base
        if starts(event, self.legato):
            voice.over = {}
            if voice.ins:
                voice.regs = {n: self._byte(records, voice, n) for n in self.names}

    def _play(self, records: dict, voice: _Voice, name: str, shift: int, nibble: int) -> int:
        """The byte the small FX setting nibble ``shift`` of ``name`` to ``nibble`` writes."""
        if not voice.ins:
            raise EnvelopeError(f"!{name} on a voice that has no instrument")
        voice.over[name] = (shift, nibble)
        for other in self.names:
            byte, was = self._byte(records, voice, other), voice.regs.get(other, 0)
            if other != name and byte != was:
                raise EnvelopeError(f"!{name} also writes {other} {byte} over {was}")
            voice.regs[other] = byte
        return voice.regs[name]

    def _written(self, records: dict, voice: _Voice, event: dict) -> list:
        """An event's commands with each small FX as the byte it writes."""
        self._enter(records, voice, event)
        cmds = event_commands(event)
        out = []
        for k, (code, arg) in enumerate(cmds):
            if code not in self.native:
                out.append((code, arg))
                continue
            name, shift, slot = self.native[code]
            written = self.slot(event, k)
            alone = len(cmds) == 1 and not starts(event, self.legato)
            if slot != written and not (slot == 1 and alone):
                column = "FX" if slot else "instrument"
                raise EnvelopeError(f"!{name} in the {column} column has no written form here")
            out.append((self.code(name), self._play(records, voice, name, shift, arg)))
        return out

    def _read(self, records: dict, voice: _Voice, event: dict) -> list:
        """An event's commands with each byte as the small FX that writes it."""
        self._enter(records, voice, event)
        out = []
        for k, (code, byte) in enumerate(event_commands(event)):
            name = self.name(code)
            if name is None:
                out.append((code, byte))
                continue
            if not voice.ins:
                raise EnvelopeError(f"!{name} on a voice that has no instrument")
            slot = self.slot(event, k)
            voice.over.pop(name, None)
            own = self._byte(records, voice, name)
            fits = [
                s
                for s in (4, 0)
                if (byte ^ own) & ~(NIBBLE << s) == 0 and (name, s, slot) in self.codes
            ]
            if not fits:
                raise EnvelopeError(
                    f"the player can't express !{name}:{byte} over the instrument's {name} {own}"
                )
            nibble = byte >> fits[0] & NIBBLE
            self._play(records, voice, name, fits[0], nibble)
            out.append((self.codes[(name, fits[0], slot)], nibble))
        return out

    def write(self, song: dict, records: dict) -> dict:
        """``song`` with each small FX of its phrases as the byte it writes."""
        return self._rewrite(song, records, self._written)

    def read(self, song: dict, records: dict) -> dict:
        """``song`` with each envelope byte of its phrases as the small FX that writes it."""
        return self._rewrite(song, records, self._read)

    def _rewrite(self, song: dict, records: dict, convert) -> dict:
        """``song`` with each event's commands as ``convert`` gives them where the voice plays
        it; a phrase played in contexts that give it different commands becomes one phrase per
        context."""
        if not self.native:
            return song
        phrases = song["phrases"]
        variants, out, new = {}, dict(phrases), []
        for trk in song["tracks"]:
            plays = list(trk["plays"])
            for k, got in _played(trk, phrases, functools.partial(convert, records)).items():
                name = plays[k]["phrase"]
                alias = _alias(variants.setdefault(name, []), name, got)
                out[alias] = [_with(e, c) for e, c in zip(phrases[name], got)]
                plays[k] = {**plays[k], "phrase": alias}
            new.append({**trk, "plays": plays})
        return {**song, "tracks": new, "phrases": out}
