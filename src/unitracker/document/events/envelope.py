"""Envelope commands: ``!ad:n`` and ``!sr:n`` as the byte a player's nibble small FX writes.

A command code with ``nibble`` (its shift) and ``column`` (its event slot, see ``column.COLUMNS``)
writes the sounding instrument's byte with that nibble replaced, and the other register again
from its override, which must leave it unchanged; overrides last until the next note start.
"""

from unitracker.playroutine.codec import ENVELOPE, command_code, command_variants, envelope_names
from unitracker.document.events import context
from unitracker.document.events.column import COLUMNS
from unitracker.document.events.lifetime import starts
from unitracker.player.vm import event_commands

NIBBLE = 0x0F


class EnvelopeError(ValueError):
    """An envelope command the player can't play where it is written."""


class _Voice:
    """One voice's instrument, envelope registers and nibble overrides."""

    # pylint: disable=too-few-public-methods

    def __init__(self):
        self.ins, self.regs, self.over = 0, {}, {}


class Envelope:
    """A playroutine's envelope commands over a song's records of types ``rtypes``."""

    def __init__(self, pr: dict, rtypes: dict):
        inst = pr.get("instrument") or {}
        self.ins_type, self.base = inst.get("type"), int(inst.get("base", 0))
        self.rtype = rtypes.get(self.ins_type)
        self.legato = command_code(pr.get("commands"), "legato")
        self.native = {}
        for code, var in command_variants(pr.get("commands")):
            if "nibble" in var:
                slot = COLUMNS.index(var["column"])
                self.native[code] = (str(var["name"]), int(var["nibble"]), slot)
        self.codes = {v: c for c, v in self.native.items()}
        self.names = envelope_names(pr.get("commands"))

    def code(self, name: str) -> int:
        """The internal code of written command ``name``, its argument the byte."""
        return ENVELOPE - self.names.index(name)

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

    def write(self, song: dict, records: dict) -> dict:
        """``song`` with each small FX of its phrases as the byte it writes."""
        return self._rewrite(song, records, self._written)

    def _rewrite(self, song: dict, records: dict, convert) -> dict:
        """``song`` with each event's commands as ``convert`` gives them where the voice plays
        it (see ``context.rewrite``)."""
        if not self.native:
            return song

        def events(voice, event):
            return [context.with_commands(event, convert(records, voice, event))]

        return context.rewrite(song, events, _Voice)
