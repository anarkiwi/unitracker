"""An event's written commands as the native command codes the reader plays."""

from unitracker.format import tempo
from unitracker.format.atoms import parse_atom
from unitracker.format.codec import (
    COMMAND_NAMES,
    NONE,
    CodecError,
    command_text,
    envelope_names,
    inline_row,
)
from unitracker.format.events import column, lifetime
from unitracker.player.vm import MAX_COMMANDS


def legato_note(kind: int, names, atom, error) -> None:
    """Raise ``error`` for ``!legato`` on an event of note ``kind`` that is no note."""
    if "legato" in names and kind != 1:
        raise error(f"!legato needs a note: {atom!r}")


class EventCommands:
    """The commands of a reader's events: their order, their arguments' names and inline rows,
    and the commands written beside another whose native effect they are; ``error`` the reader's
    exception."""

    def __init__(self, reader, error):
        self.reader, self.error = reader, error
        self.name, self.codec = reader.pr["name"], reader.commands
        self.slotted = envelope_names(reader.pr.get("commands"))

    def decode(self, parsed: dict, atom) -> list:
        """``[[code, arg]]`` of a parsed atom's slot commands."""
        written = [c for c in parsed["commands"] if not lifetime.is_lifetime(*c)]
        kept = [c for c in parsed["commands"] if lifetime.is_lifetime(*c)]
        names = [c for c, _ in written]
        if parsed["commands"] != written + sorted(kept, key=lambda c: lifetime.NAMES.index(c[0])):
            raise self.error(f"!pw and !filter follow the other commands, in that order: {atom!r}")
        legato_note(parsed["kind"], names, atom, self.error)
        if names != column.ordered(names, self.slotted):
            order = "!" + ", !".join(column.ordered(names))
            raise self.error(f"an event's commands are written in the order {order}: {atom!r}")
        commands = self._companions(written, [self.command(c, a, atom) for c, a in written], atom)
        if len(commands) > MAX_COMMANDS:
            raise self.error(f"more than {MAX_COMMANDS} commands in {atom!r}")
        return commands

    def _companions(self, written: list, commands: list, atom) -> list:
        """``commands`` (decoded from ``written``) without the commands written beside another
        whose native effect they are (``also``); raises for one written without its companion."""
        drop = set()
        for (name, arg), (code, raw) in zip(written, commands):
            also = self.codec.also(code, raw)
            if also is None:
                continue
            companion = parse_atom(f"-!{also}")["commands"][0]
            if companion not in written:
                text = command_text(name, arg)
                raise self.error(f"{self.name}: {text} also does !{also}: write both in {atom!r}")
            drop.add(written.index(companion))
        return [c for k, c in enumerate(commands) if k not in drop]

    def command(self, cmd, arg, atom) -> list[int]:
        """``[code, arg]`` for one written command of ``atom``."""
        if cmd not in COMMAND_NAMES:
            raise self.error(f"unknown command !{cmd} in {atom!r}")
        single = tempo.one(cmd, arg)
        if single is not None:
            raise self.error(f"!{cmd}:{single}/{single} is written !{cmd}:{single} in {atom!r}")
        if self.codec.inline(cmd) and isinstance(arg, tuple):
            arg = self._inline(cmd, arg, atom)
        elif isinstance(arg, str) and arg != NONE:
            if arg not in self.reader.names:
                raise self.error(f"unknown name {arg!r} in {atom!r}")
            arg = self.reader.names[arg]
        try:
            return list(self.codec.decode(cmd, arg))
        except CodecError as exc:
            problem = self.error(f"{self.name}: {exc} in {atom!r}")
        if cmd in tempo.COMMANDS and self.codec.inline(cmd) and isinstance(arg, int):
            try:
                return list(self.codec.decode(cmd, self._inline(cmd, (arg, arg), atom)))
            except (CodecError, self.error):
                pass
        raise problem

    def _inline(self, cmd: str, values, atom) -> tuple:
        """``(type, index)`` of the row an inline argument writes: an equal row already read, else
        one allocated for it."""
        rtype = self.reader.rtypes[self.codec.inline(cmd)]
        try:
            row = inline_row(rtype, values)
        except CodecError as exc:
            raise self.error(f"!{cmd}: {exc} in {atom!r}") from exc
        try:
            self.codec.check_inline(cmd, row)
        except CodecError as exc:
            raise self.error(f"{self.name}: {exc} in {atom!r}") from exc
        rows = self.reader.records[rtype.name]
        index = next((i for i, r in rows.items() if r == row), None)
        if index is None:
            index = self.reader._alloc(rtype, 1)  # pylint: disable=protected-access
            rows[index] = row
        return rtype.name, index
