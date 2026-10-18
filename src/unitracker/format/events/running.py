"""Running values: what each command a voice plays has set, as the walk through its plays knows
it. A command that sets what is already set changes nothing: the writer leaves it out and the
reader refuses it. ``!freq`` lasts until a note starts the instrument (docs/format/vocabulary.md,
*Running values* and *Lifetime of `!freq`*)."""

from unitracker.format import tempo
from unitracker.format.codec import EXPR, NONE, CodecError, command_code, command_variants
from unitracker.format.events import context, restart
from unitracker.format.events.lifetime import order, starts
from unitracker.format.xexpr import ExpressionError, canonical
from unitracker.player.vm import event_commands

NAME = "freq"
SONG = ("tempo", "vtempo", "vol", "cut", "resfilt")
TEMPOS = ("tempo", "vtempo")
BYTES = ("ad", "sr")
KEYS = {
    "ad": {"ad"},
    "sr": {"sr"},
    "ctrl": {"ctrl", "gate"},
    "vol": {"vol"},
    "cut": {"cut"},
    "resfilt": {"res", "route", "filter"},
}
PROGRAM = "program"


def lasts_step(pr: dict) -> bool:
    """Whether playroutine ``pr``'s native ``!freq`` ends on a step without a command."""
    cmds = pr.get("commands")
    code = command_code(cmds, NAME)
    return any(v.get("lasts") == "step" for c, v in command_variants(cmds) if c == code)


class RunningError(ValueError):
    """A command the player can't play where it is written, or one that changes nothing."""


class _Voice:
    """A voice's running ``!freq`` (None: the instrument's) and instrument, and what its phrase
    has set: ``own`` by command name (a program slot by its record type), and ``named``, the
    instrument the phrase last named."""

    # pylint: disable=too-few-public-methods

    def __init__(self):
        self.x = self.ins = self.named = None
        self.own = {}

    def enter(self) -> None:
        """Start a phrase play."""
        self.own, self.named = {}, None


def _text(text: str) -> str:
    """The one text of expression ``text``, or ``text`` outside the grammar."""
    try:
        return canonical(text)
    except ExpressionError:
        return text


class Running:
    """A playroutine's commands over a song: its ``commands`` codec, record types, the codecs of
    their rows, its ``records`` and its expression texts ``exprs``, which compare by their one
    text."""

    # pylint: disable=too-many-instance-attributes,too-many-arguments,too-many-positional-arguments

    def __init__(self, pr, rtypes, commands, codecs, records, exprs):
        self.code = command_code(pr.get("commands"), NAME)
        self.step = lasts_step(pr)
        self.legato = command_code(pr.get("commands"), "legato")
        self.commands, self.rtypes, self.codecs, self.records = commands, rtypes, codecs, records
        inst = pr.get("instrument") or {}
        self.ins_type, self.base = inst.get("type"), int(inst.get("base", 0))
        texts = {int(i): _text(str(t)) for i, t in (exprs or {}).items()}
        canon = {}
        self.same = {i: canon.setdefault(t, i) for i, t in sorted(texts.items())}
        self.held = {i for i, t in texts.items() if t == "v"}
        self.unknown = set()

    # -- values --

    def _row(self, index) -> dict | None:
        return None if index is None else self.records.get(self.ins_type, {}).get(index)

    def value(self, code: int, arg: int) -> tuple | None:
        """``(key, value)`` of a native command, None for one that sets no value."""
        try:
            name, value = self.commands.encode(code, arg)
        except CodecError:
            return None
        ref = self.commands.ref(code)
        if name == "legato":
            return None
        if ref is not None and self.rtypes[ref].chain is not None:
            return ref, value if value == NONE else PROGRAM
        if isinstance(value, tuple) and value[:1] == (EXPR,):
            value = (EXPR, self.same.get(value[1], value[1]))
        elif isinstance(value, tuple) and self.commands.inline(code):
            rtype = self.rtypes[value[0]]
            row = self.records.get(rtype.name, {}).get(value[1], {})
            value = tuple(int(row.get(f, rtype.default(f))) for f in rtype.own)
            value = tempo.one(name, value) or value
        return name, value

    def _no_freq(self, voice: _Voice) -> bool:
        """Whether the voice plays an instrument known to have no ``freq``."""
        row = self._row(voice.ins)
        return row is not None and not int(row.get(NAME, 0))

    def known(self, song: dict) -> None:
        """Set :attr:`unknown`, the command keys whose value the walk can't know in ``song``:
        those a program row sets, and the song's own that more than one voice sets."""
        keys = set()
        for name, codec in self.codecs.items():
            if self.rtypes[name].chain is None or name == restart.NAME:
                continue
            for row in self.records.get(name, {}).values():
                keys.update(codec.encode(row) or {})
        self.unknown = {n for n, ks in KEYS.items() if ks & keys}
        voices = {}
        for v, trk in enumerate(song["tracks"]):
            for _, phrase in order(trk):
                for event in song["phrases"][phrase]:
                    for code, arg in event_commands(event):
                        key = self.value(code, arg)
                        if key is not None and key[0] in SONG:
                            voices.setdefault(key[0], set()).add(v)
        self.unknown |= {n for n, vs in voices.items() if len(vs) > 1}
        if self.unknown & set(TEMPOS):
            self.unknown |= set(TEMPOS)

    # -- the walk --

    def _start(self, voice: _Voice, event: dict) -> None:
        """Name the event's instrument, and start its note."""
        if event.get("instrument"):
            voice.ins = voice.named = int(event["instrument"]) - self.base
        if not starts(event, self.legato):
            return
        voice.x = None
        voice.own = {k: v for k, v in voice.own.items() if k in SONG}
        row = self._row(voice.named)
        if row is None:
            return
        rtype = self.rtypes[self.ins_type]
        for name in BYTES:
            if name in rtype.fields and name not in self.unknown:
                voice.own[name] = int(row.get(name, rtype.default(name)))
        if not self.step and int(row.get(NAME, 0)):
            voice.own[NAME] = (EXPR, self.same.get(int(row[NAME]), int(row[NAME])))

    def _freq(self, voice: _Voice, value, arg: int) -> str | None:
        """Play ``!freq`` of ``value`` (native argument ``arg``): ``"none"`` where it is written
        ``!freq:none``, ``"same"`` where it changes nothing, else None."""
        held = value != NONE and arg in self.held and self.step and self._no_freq(voice)
        if value == NONE or held:
            same = voice.x is None
            voice.x = None
            voice.own.pop(NAME, None)
            return "same" if same else ("none" if held else None)
        same = voice.own.get(NAME) == value
        voice.x, voice.own[NAME] = arg, value
        return "same" if same else None

    def _set(self, voice: _Voice, key, value) -> bool:
        """Play a command setting ``key`` to ``value``; whether it changes nothing."""
        tracked = value != PROGRAM and key not in self.unknown
        tracked = tracked and not (key in TEMPOS and isinstance(value, tuple))
        same = tracked and voice.own.get(key) == value
        if tracked:
            voice.own[key] = value
        else:
            voice.own.pop(key, None)
        if key == "tempo" and tracked:
            voice.own["vtempo"] = value
        elif key == "vtempo":
            voice.own.pop("tempo", None)
        return same

    def _settle(self, voice: _Voice, event: dict) -> list:
        """``[(code, arg, verdict)]`` for each command of ``event`` where the voice plays it:
        ``"same"`` for one that changes nothing, ``"none"`` for a ``!freq`` written
        ``!freq:none``, else None."""
        self._start(voice, event)
        out = []
        for code, arg in event_commands(event):
            key = self.value(code, arg)
            if key is None:
                out.append((code, arg, None))
            elif code == self.code:
                if arg == 0 and not self.step:
                    raise RunningError(f"the player can't express !{NAME}:none")
                out.append((code, arg, self._freq(voice, key[1], arg)))
            else:
                out.append((code, arg, "same" if self._set(voice, *key) else None))
        if self.step and not (voice.x is None and self._no_freq(voice)):
            if all(v == "same" or c == self.legato for c, _, v in out):
                out = [(c, a, None if c != self.code else v) for c, a, v in out]
        return out

    # -- lasts: step --

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
        """An event as written: what changes nothing left out, and ``!freq:none`` where the
        native ``!freq`` ends."""
        settled = self._settle(voice, event)
        cmds = [(c, 0 if v == "none" else a) for c, a, v in settled if v != "same"]
        if self._ends(voice, [(c, a) for c, a, _ in settled]):
            cmds = [*cmds, *self._resumed(voice)]
        return self._steps(voice, event, cmds, self._resumed)

    def _resumed(self, voice: _Voice) -> list:
        voice.x = None
        voice.own.pop(NAME, None)
        return [(self.code, 0)]

    def _read(self, voice: _Voice, event: dict) -> list:
        """An event as the player plays it, refused where a command changes nothing; the running
        ``!freq`` repeated on each step where the native one would end."""
        settled = self._settle(voice, event)
        for code, arg, verdict in settled:
            if verdict == "same":
                name = self.commands.encode(code, arg)[0]
                raise RunningError(f"!{name} sets what the voice already has: leave it out")
            if verdict == "none":
                raise RunningError(
                    f"!{NAME} of an expression that holds the frequency, on an instrument without"
                    f" {NAME}, is written !{NAME}:none"
                )
        cmds = [(c, a) for c, a, _ in settled]
        if self._ends(voice, cmds):
            cmds = [*cmds, (self.code, self._repeat(voice))]
        return self._steps(voice, event, cmds, lambda v: [(self.code, self._repeat(v))])

    def _repeat(self, voice: _Voice) -> int:
        """The running ``!freq`` a step without a command continues, which its phrase wrote."""
        if voice.own.get(NAME) != (EXPR, self.same.get(voice.x, voice.x)):
            raise RunningError(
                f"a step without a command continues the !{NAME} of the phrase before it: write"
                f" !{NAME} or !{NAME}:none on it"
            )
        return voice.x

    def write(self, song: dict) -> dict:
        """``song`` with its commands as written."""
        if self.code is None and not self.commands.entries:
            return song
        self.known(song)
        return context.rewrite(song, self._written, _Voice)

    def read(self, song: dict) -> dict:
        """``song`` with its written commands as the player plays them."""
        if self.code is None and not self.commands.entries:
            return song
        self.known(song)
        return context.rewrite(song, self._read, _Voice)
