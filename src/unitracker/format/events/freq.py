"""``!freq`` lifetime: a ``!freq:x`` lasts until a note starts the instrument or another
``!freq`` replaces it, and ``!freq:none`` returns to the instrument's ``freq``.

A playroutine whose native ``!freq`` ends on a step that carries no command declares ``lasts:
step`` on its ``freq`` entry: there the writer writes ``!freq:none``, and the reader repeats the
running ``!freq`` on each step without a command. In every player a phrase writes the ``!freq``
it runs once.
"""

from unitracker.format.codec import command_code, command_variants
from unitracker.format.events import context
from unitracker.format.events.lifetime import starts
from unitracker.player.vm import event_commands

NAME = "freq"


class FreqError(ValueError):
    """A ``!freq`` the player can't play where it is written."""


class _Voice:
    """The ``!freq`` a voice runs: its expression, or None for the instrument's; and the one the
    phrase it plays started, which it writes once."""

    def __init__(self):
        self.x = self.own = None

    def enter(self) -> None:
        """Start a phrase play."""
        self.own = None


class Freq:
    """A playroutine's ``!freq`` command over its voices; ``same`` maps an expression index to
    the first of those whose text is equal."""

    def __init__(self, pr: dict, same=None):
        cmds = pr.get("commands")
        self.same = same or {}
        self.code = command_code(cmds, NAME)
        spec = next((v for c, v in command_variants(cmds) if c == self.code), {})
        self.step = spec.get("lasts") == "step"
        self.legato = command_code(cmds, "legato")

    def _start(self, voice: _Voice, event: dict) -> tuple[list, list]:
        """``(the event's commands, its !freq arguments)``, the voice's ``!freq`` ended when the
        event starts a note."""
        if starts(event, self.legato):
            voice.x = voice.own = None
        cmds = [tuple(c) for c in event_commands(event)]
        freq = [a for c, a in cmds if c == self.code]
        if freq and freq[0] == 0 and not self.step:
            raise FreqError(f"the player can't express !{NAME}:none")
        return cmds, freq

    def _ends(self, voice: _Voice, cmds: list) -> bool:
        """Whether a step of commands ``cmds`` ends the voice's native ``!freq``."""
        return self.step and voice.x is not None and all(c == self.legato for c, _ in cmds)

    def _steps(self, voice: _Voice, event: dict, cmds: list, empty) -> list:
        """``event`` with ``cmds``, then its empty steps, each of which ``empty(voice)`` gives
        the commands of while the native ``!freq`` would end there."""
        steps = int(event.get("steps", 1))
        first = {k: v for k, v in event.items() if k != "steps"}
        out, rest = [context.with_commands(first, cmds)], steps - 1
        while rest and self._ends(voice, []):
            out.append(context.with_commands({}, empty(voice)))
            rest -= 1
        if rest and len(out) == 1:
            out[0]["steps"] = rest + 1
        elif rest:
            out.append({"steps": rest} if rest > 1 else {})
        return out

    def _written(self, voice: _Voice, event: dict) -> list:
        """An event as written: a running ``!freq`` left out, and ``!freq:none`` on each step
        where the native ``!freq`` ends."""
        cmds, freq = self._start(voice, event)
        if freq:
            x = self.same.get(freq[0], freq[0]) or None
            if x is not None and x == voice.own == voice.x:
                cmds = [c for c in cmds if c[0] != self.code]
            voice.x = voice.own = x
        elif self._ends(voice, cmds):
            voice.x = voice.own = None
            cmds = [*cmds, (self.code, 0)]
        return self._steps(voice, event, cmds, self._resumed)

    def _resumed(self, voice: _Voice) -> list:
        voice.x = voice.own = None
        return [(self.code, 0)]

    def _read(self, voice: _Voice, event: dict) -> list:
        """An event as the player plays it: the running ``!freq`` repeated on each step where the
        native one would end."""
        cmds, freq = self._start(voice, event)
        if freq:
            if freq[0] and freq[0] == voice.own == voice.x:
                raise FreqError(f"!{NAME} repeats the !{NAME} its phrase runs")
            voice.x = voice.own = freq[0] or None
        elif self._ends(voice, cmds):
            cmds = [*cmds, (self.code, voice.x)]
        return self._steps(voice, event, cmds, lambda v: [(self.code, v.x)])

    def write(self, song: dict) -> dict:
        """``song`` with its ``!freq`` commands as written."""
        return self._rewrite(song, self._written)

    def read(self, song: dict) -> dict:
        """``song`` with its written ``!freq`` commands as the player plays them."""
        return self._rewrite(song, self._read)

    def _rewrite(self, song: dict, convert) -> dict:
        if self.code is None:
            return song
        return context.rewrite(song, convert, _Voice)
