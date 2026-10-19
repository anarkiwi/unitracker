"""Instrument names a note need not carry: a note that names the instrument its phrase last
named plays it whether or not it names it, unless the instrument keeps a program by a lifetime
flag (naming it restarts the program) or a command of the note is read into another column when
it names one. A playroutine whose events arm programs through their instrument (an event
instrument, see ``instrument``) names each one.
"""

from unitracker.playroutine.codec import ENVELOPE, command_variants
from unitracker.document.events import instrument
from unitracker.document.events.lifetime import Lifetime, notes
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

    def redundant(self, events: list, rows: dict, flagged=None, same=None):
        """The index of each note of a phrase's ``events`` that names the instrument its phrase
        last named, where naming it changes nothing; ``rows`` the instrument records,
        ``flagged`` the instruments whose flags are in use (default: every one), and ``same``
        what an instrument index is written as (default: itself)."""
        if self.off:
            return
        named = None
        for k, event in enumerate(events):
            ins = event.get("instrument")
            if not ins:
                continue
            note, index = event.get("note"), int(ins) - self.lifetime.base
            ident = index if same is None else same(index)
            if ident == named and isinstance(note, int) and not isinstance(note, bool):
                used = flagged is None or index in flagged
                if not self._changes(event, rows.get(index), used):
                    yield k
            named = ident

    @staticmethod
    def _dropped(events: list, drop: set) -> list:
        return [
            {f: v for f, v in e.items() if f != "instrument"} if k in drop else e
            for k, e in enumerate(events)
        ]

    def same_written(self, phrases: dict, rows: dict, same) -> dict:
        """``phrases`` without the names that change nothing once equal instruments are one
        written record, ``same`` giving what an instrument index is written as."""
        return {
            name: self._dropped(events, set(self.redundant(events, rows, same=same)))
            for name, events in phrases.items()
        }

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
            out[name] = self._dropped(events, set(self.redundant(events, rows, flagged)))
        return out, (lifetime.unflagged(rows, flagged) if lifetime.flagged else rows)
