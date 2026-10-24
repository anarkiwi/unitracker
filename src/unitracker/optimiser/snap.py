"""The ``snap`` pass: a phrase event's frequency binding under which the voice's frequency is
``F(N)`` at every tick it lasts, as a lifted "change pitch without retrigger" (a ``legato``
note bound to a modulator that jumps to the note) is, becomes the cell's own ``F(N)``: the
binding dropped, or a ``none`` of each frequency slot bound as it applied (docs/optimiser.md)."""

from collections import defaultdict
from dataclasses import replace
from functools import partial

from unitracker_grammar.document import NONE, Document, base, printed
from unitracker_player import compiler
from unitracker_player.document import read
from unitracker_player.player import Player

from unitracker.optimiser.passes import Edit, _label
from unitracker.optimiser.tree import update

_SITE = 6  # a binding site, after the player's ``[fn, phi, voice, name, command, once]``


class _Site:  # pylint: disable=too-few-public-methods
    """What a binding site did over a play: the slots bound as it applied, and whether the
    frequency was ``F(N)`` at every tick its binding lasted, and after it while a slot it
    would end stayed bound."""

    def __init__(self):
        self.bound, self.exact, self.ticks = set(), True, 0


class _Tracer(Player):
    """A player that keeps, by phrase event and frequency slot, each binding command's
    :class:`_Site`; ``pending`` holds per voice each site's slots its ``none`` would leave
    unbound that nothing has bound or stopped since."""

    def __init__(self, song, where: dict):
        self.where, self.site, self.sites = where, None, defaultdict(_Site)
        self.pending = defaultdict(dict)
        super().__init__(song)

    # pylint: disable-next=too-many-arguments,too-many-positional-arguments
    def _event(self, t, v, event, transpose, ins, last, part):
        self.site = self.where.get(id(event))
        try:
            super()._event(t, v, event, transpose, ins, last, part)
        finally:
            self.site = None

    def _settle(self, v: int, slots):
        for left in self.pending[v].values():
            left.difference_update(slots)

    # pylint: disable-next=too-many-arguments,too-many-positional-arguments
    def _set(self, v: int, cell: str, slot: int, fn, once: bool, command: bool, fresh=False):
        super()._set(v, cell, slot, fn, once, command, fresh)
        if cell == "freq":
            self._settle(v, (slot,))

    def _stop(self, v: int, cell: str, value, ends: tuple):
        super()._stop(v, cell, value, ends)
        if cell == "freq":
            self._settle(v, ends)

    def _command(self, v: int, key: str, value, t: int):
        binds = key == "freq" and isinstance(value[0], int) and self.site is not None
        if binds:
            mods, key_ = self.voices[v].freq_mods, self.site + (value[0],)
            bound = {k for k, mod in enumerate(mods) if mod is not None}
            self.sites[key_].bound |= bound | ({value[0]} if self.voices[v].freq_hold else set())
        super()._command(v, key, value, t)
        if binds:
            self.voices[v].freq_mods[value[0]][_SITE:] = [key_]
            self.pending[v][key_] = bound - {value[0]}

    def _fire_freq(self, t: int, vix: int):
        super()._fire_freq(t, vix)
        voice, pending = self.voices[vix], self.pending[vix]
        active = {mod[_SITE] for mod in voice.freq_mods if mod is not None and len(mod) > _SITE}
        for key in active | {k for k, left in pending.items() if left}:
            site, note = self.sites[key], voice.sounding
            site.ticks += 1
            site.exact &= note is not None and voice.freq == self.song.table(note)


def sites(text: str, ticks: int) -> dict:
    """``{(phrase, index, slot): _Site}`` of each phrase event's frequency binding command over
    ``ticks`` ticks of ``text``'s play."""
    where = {}

    class _Compiler(compiler._Compiler):  # pylint: disable=protected-access
        def phrase(self, name):
            out = super().phrase(name)
            if isinstance(name, str):
                where.update({id(e): (name, i) for i, e in enumerate(out)})
            return out

    player = _Tracer(compiler.compile_document(read(text), _Compiler), where)
    while player.tick < ticks and player.step() is not None:
        pass
    return dict(player.sites)


def _snapped(phrase: str, index: int, pair: tuple, nones: tuple, doc: Document) -> Document:
    """``doc`` with phrase event ``index``'s command ``pair`` replaced by ``nones``."""

    def edit(event):
        commands = list(event.commands)
        at = commands.index(pair)
        commands[at : at + 1] = [n for n in nones if n not in commands]
        return replace(event, commands=tuple(commands))

    path = next(
        ("defs", i, "records", j, 1, index)
        for i, section in enumerate(doc.defs)
        for j, (name, _) in enumerate(section.records)
        if section.kind == "phrase" and name == phrase
    )
    return update(doc, path, edit)


def snap(doc: Document, ticks: int) -> list:
    """Each phrase event's frequency binding under which the frequency was ``F(N)`` at every
    tick it lasted over ``ticks`` ticks of ``doc``'s play becomes ``none`` of each slot
    bound as it applied (the frequency then ``F(N)`` by default), dropped where none was."""
    names = [s.name for s in doc.header.slot("freq")]
    phrases, edits = doc.records("phrase"), []
    for (phrase, index, slot), site in sorted(sites(printed(doc), ticks).items()):
        if not site.exact or not site.ticks:
            continue
        event = phrases[phrase][index]
        pairs = [
            (k, v)
            for k, v in event.commands
            if base(k) == "freq" and (k.rpartition(".")[0] or names[0]) == names[slot]
        ]
        if len(pairs) != 1 or pairs[0][1] == NONE:
            continue
        nones = tuple((names[k] + ".freq" if k else "freq", NONE) for k in sorted(site.bound))
        label = _label((phrase, index), f"{pairs[0][0]}:{pairs[0][1]}")
        edits.append(Edit(label, partial(_snapped, phrase, index, pairs[0], nones)))
    return edits


snap.traced = True  # the driver passes the guarantee's horizon
