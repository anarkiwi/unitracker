"""Event folds: the records a document's events name for what the native events hold on several
steps (a note's restart, see ``restart``) or in several columns (its instrument, see
``instrument``). The writer folds a compiled phrase's columns into them, the reader unfolds them.
"""

from unitracker.format.events import instrument, restart


class FoldError(ValueError):
    """A phrase's events that its folds can't express."""


class EventFolds:
    """A playroutine's event folds over one song's ``records``, which the folds extend."""

    # pylint: disable=too-many-instance-attributes

    def __init__(self, pr: dict, rtypes: dict, codecs: dict, commands, records: dict):
        self.rtypes, self.codecs, self.records = rtypes, codecs, records
        spec = pr.get(restart.NAME)
        self.command = int(spec["command"]) if spec else None
        self.target = rtypes[commands.ref(self.command)] if spec else None
        self.ins = instrument.spec(pr)
        self.base = int((pr.get("instrument") or {}).get("base", 0))
        self.restarts, self.played, self.instruments = {}, {}, {}

    def held(self, tname: str) -> bool:
        """Whether only a document holds type ``tname``'s records, which its events name."""
        if tname == restart.NAME:
            return self.command is not None
        return self.ins is not None and tname == self.ins["type"]

    # -- writing --

    def fold(self, arr, program):
        """A compiled phrase with its restarts on their notes and its instruments named;
        ``program(rtype, entry)`` is a program's ``(row indices, loop index or None)``."""
        try:
            if self.command is not None:
                arr = restart.fold(
                    arr, self.command, lambda entry, gap: self._restart_of(entry, gap, program)
                )
            if self.ins is not None:
                arr = instrument.fold(arr, self.ins, self.base, self._instrument)
        except instrument.InstrumentError as exc:
            raise FoldError(str(exc)) from exc
        return arr

    def _restart_of(self, entry: int, gap: int, program):
        """The restart index of program ``entry`` run ``gap`` steps before a note, or None."""
        rtype, codec = self.rtypes[restart.NAME], self.codecs[restart.NAME]
        path, loop = program(self.target, entry)
        if loop is not None:
            return None
        rows = [{**self.target.fields, **self.records[self.target.name][i]} for i in path]
        raws = restart.restart_rows(rows, self.target, rtype, codec, gap)
        if raws is None:
            return None
        table = self.records.setdefault(rtype.name, {})
        return restart.entry_of(raws, rtype, table, self.restarts)

    def _instrument(self, fields: dict) -> int:
        """The index of the instrument holding ``fields``, allocated on first use."""
        key = tuple(sorted(fields.items()))
        if key not in self.instruments:
            rtype = self.rtypes[self.ins["type"]]
            table = self.records.setdefault(rtype.name, {})
            self.instruments[key] = max([rtype.first - 1, *table]) + 1
            table[self.instruments[key]] = dict(fields)
        return self.instruments[key]

    # -- reading --

    def unfold(self, events: list, alloc) -> list:
        """A phrase's events with each named instrument as the columns it arms and each restart
        played before its note; ``alloc(rtype, count)`` allocates a restart's program rows."""
        try:
            if self.ins is not None:
                rtype = self.rtypes[self.ins["type"]]
                rows = self.records.get(rtype.name, {})
                events = [
                    (
                        instrument.unfold(e, rows[e["instrument"]], self.ins, rtype, self.base)
                        if e.get("instrument")
                        else e
                    )
                    for e in events
                ]
            if any("restart" in e for e in events):
                return restart.unfold(events, self.command, lambda i: self._played(i, alloc))
        except (instrument.InstrumentError, restart.RestartError) as exc:
            raise FoldError(str(exc)) from exc
        return events

    def _played(self, index: int, alloc) -> tuple:
        """``(entry, steps)``: the program event restart ``index`` runs, and how far ahead."""
        if index not in self.played:
            rtype, codec = self.rtypes[restart.NAME], self.codecs[restart.NAME]
            self.played[index] = restart.played(
                index, self.records, (rtype, self.target), codec, alloc
            )
        return self.played[index]
