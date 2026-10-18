"""Instrument names a note need not carry: a note that names the instrument its phrase last
named plays it whether or not it names it, unless the instrument keeps a program by a lifetime
flag (naming it restarts the program) or a command of the note is read into another column when
it names one. A playroutine whose events arm programs through their instrument (an event
instrument, see ``instrument``) names each one.
"""

from unitracker.format.codec import ENVELOPE, command_variants
from unitracker.format.events import instrument
from unitracker.format.events.lifetime import Lifetime, notes
from unitracker.player.vm import event_commands


class NamingError(ValueError):
    """A note that names the instrument it plays without naming it."""


class Naming:
    """A playroutine's redundant instrument names over a song's records of types ``rtypes``."""

    def __init__(self, pr: dict, rtypes: dict):
        self.off = instrument.spec(pr) is not None
        self.lifetime = Lifetime(pr, rtypes)
        self.moved = {
            code
            for code, var in command_variants(pr.get("commands"))
            if "nibble" in var or isinstance(var.get("column"), list)
        }

    def _changes(self, event: dict, row: dict | None, flagged: bool) -> bool:
        """Whether a note naming instrument ``row`` plays otherwise than without the name;
        ``flagged`` whether the instrument's lifetime flags are in use."""
        if row is None or any(c in self.moved or c <= ENVELOPE for c, _ in event_commands(event)):
            return True
        return flagged and any(self.lifetime.keeps(row, cmd) for cmd in self.lifetime.flagged)

    def redundant(self, events: list, rows: dict, flagged=None):
        """The index of each note of a phrase's ``events`` that names the instrument its phrase
        last named, where naming it changes nothing; ``rows`` the instrument records, and
        ``flagged`` the instruments whose flags are in use (default: every one)."""
        if self.off:
            return
        named = None
        for k, event in enumerate(events):
            ins = event.get("instrument")
            if not ins:
                continue
            note, index = event.get("note"), int(ins) - self.lifetime.base
            if ins == named and isinstance(note, int) and not isinstance(note, bool):
                used = flagged is None or index in flagged
                if not self._changes(event, rows.get(index), used):
                    yield k
            named = ins

    def write(self, tracks: list, phrases: dict, rows: dict) -> tuple[dict, dict]:
        """``(phrases, rows)`` without the instrument names that change nothing, and without the
        lifetime flags no note starting an instrument without naming it reads."""
        lifetime = self.lifetime
        flagged = {
            ins
            for _, _, ins, selects in notes(tracks, phrases, lifetime.base, lifetime.legato)
            if not selects
        }
        out = {}
        for name, events in phrases.items():
            drop = set(self.redundant(events, rows, flagged))
            out[name] = [
                {f: v for f, v in e.items() if f != "instrument"} if k in drop else e
                for k, e in enumerate(events)
            ]
        return out, (lifetime.unflagged(rows, flagged) if lifetime.flagged else rows)

    def check(self, phrases: dict, rows: dict) -> None:
        """Refuse a phrase with a note that names the instrument its phrase last named, where
        naming it changes nothing."""
        for name, events in phrases.items():
            for k in self.redundant(events, rows):
                raise NamingError(
                    f"note {k} of phrase {name} names the instrument the phrase plays:"
                    " leave the name out"
                )
