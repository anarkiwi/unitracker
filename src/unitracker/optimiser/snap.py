"""The ``snap`` pass: a phrase event's frequency binding under which the voice's frequency is
``F(N)`` at every tick it lasts, as a lifted "change pitch without retrigger" (a ``legato``
note bound to a modulator that jumps to the note) is, becomes the cell's own ``F(N)``: the
binding dropped, or a ``none`` of each frequency slot bound as it applied (docs/optimiser.md)."""

from collections import defaultdict
from dataclasses import replace
from functools import partial

from unitracker_grammar.document import NONE, Document, Group, base, printed
from unitracker_grammar.document import read as grammar_read
from unitracker_player import compiler
from unitracker_player.document import read
from unitracker_player.player import Player

from unitracker.optimiser.passes import Edit, _label
from unitracker.optimiser.tree import at, update


class _Site:  # pylint: disable=too-few-public-methods
    """What a binding site did over a play: the slots bound as it applied, and whether the
    frequency was ``F(N)`` at every tick its binding lasted, and after it while a slot it
    would end stayed bound."""

    def __init__(self):
        self.bound, self.exact, self.ticks = set(), True, 0


class _Observer:
    """Keeps, by phrase event and frequency slot, each binding command's :class:`_Site`;
    ``pending`` holds per voice each site's slots its ``none`` would leave unbound that nothing
    has bound or stopped since, ``tags`` the site of each modulator a command bound."""

    def __init__(self, where: dict):
        self.where, self.player, self.site, self.sites = where, None, None, defaultdict(_Site)
        self.pending, self.tags, self.new = defaultdict(dict), {}, {}

    def __call__(self, kind: str, *args):
        getattr(self, kind, lambda *_: None)(*args)

    def event(self, _voice, event, _ins):
        """Notes the phrase event applying."""
        self.site = self.where.get(id(event))

    def _settle(self, v: int, slots):
        for left in self.pending[v].values():
            left.difference_update(slots)

    def bind(self, v: int, cell: str, slot: int, command: bool, *_):
        """Notes a frequency binding about to be set: the slots bound as it applies."""
        if cell != "freq":
            return
        self._settle(v, (slot,))
        if command and self.site is not None:
            voice, key = self.player.voices[v], self.site + (slot,)
            bound = {k for k, mod in enumerate(voice.mods["freq"]) if mod is not None}
            self.sites[key].bound |= bound | ({slot} if voice.freq_hold else set())
            self.pending[v][key] = bound - {slot}
            self.new[v, slot] = key

    def stop(self, v: int, cell: str, _value, ends: tuple):
        """Notes frequency slots stopped."""
        if cell == "freq":
            self._settle(v, ends)

    def cell(self, cell: str, v: int):
        """Scores the frequency each site's binding left once the voice's frequency settles."""
        if cell != "freq":
            return
        voice, mods = self.player.voices[v], self.player.voices[v].mods["freq"]
        for (u, slot), key in list(self.new.items()):
            if u == v:
                del self.new[u, slot]
                if mods[slot] is not None:
                    self.tags[id(mods[slot])] = (mods[slot], key)
        active = {self.tags[id(mod)][1] for mod in mods if mod is not None and id(mod) in self.tags}
        note = voice.sounding
        for key in active | {k for k, left in self.pending[v].items() if left}:
            site = self.sites[key]
            site.ticks += 1
            site.exact &= note is not None and voice.freq == self.player.song.table(note)


def printed_phrases(text: str) -> dict:
    """``text``'s phrases by name as the document keeps them, groups and all."""
    return grammar_read(text).records("phrase")


def _paths(items: tuple, prefix: tuple = ()) -> list:
    """The path below a phrase body of each event of ``items`` once its groups are played out,
    in the order the compiler expands them (``unitracker_grammar.expand.flat``)."""
    out = []
    for i, item in enumerate(items):
        if not isinstance(item, Group):
            out.append(prefix + (i,))
        elif item.endings:
            for k, ending in enumerate(item.endings):
                out += _paths(item.body, prefix + (i, "body"))
                out += _paths(ending, prefix + (i, "endings", k))
        else:
            out += _paths(item.body, prefix + (i, "body")) * item.times
    return out


def sites(text: str, ticks: int) -> dict:
    """``{(phrase, path, slot): _Site}`` of each phrase event's frequency binding command over
    ``ticks`` ticks of ``text``'s play; ``path`` runs from the phrase's body through its groups
    to the event."""
    where, bodies = {}, printed_phrases(text)

    class _Compiler(compiler._Compiler):  # pylint: disable=protected-access
        def phrase(self, name):
            out = super().phrase(name)
            paths = _paths(bodies[name]) if isinstance(name, str) and name in bodies else ()
            if len(paths) == len(out):
                where.update({id(e): (name, path) for path, e in zip(paths, out)})
            return out

    seen = _Observer(where)
    player = seen.player = Player(compiler.compile_document(read(text), _Compiler), seen)
    while player.tick < ticks and player.step() is not None:
        pass
    return dict(seen.sites)


def _snapped(phrase: str, index: tuple, pair: tuple, nones: tuple, doc: Document) -> Document:
    """``doc`` with the command ``pair`` of the phrase event at path ``index`` replaced by
    ``nones``."""

    def edit(event):
        commands = list(event.commands)
        k = commands.index(pair)
        commands[k : k + 1] = [n for n in nones if n not in commands]
        return replace(event, commands=tuple(commands))

    path = next(
        ("defs", i, "records", j, 1, *index)
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
        event = at(phrases[phrase], index)
        pairs = [
            (k, v)
            for k, v in event.commands
            if base(k) == "freq" and (k.rpartition(".")[0] or names[0]) == names[slot]
        ]
        if len(pairs) != 1 or pairs[0][1] == NONE:
            continue
        nones = tuple((names[k] + ".freq" if k else "freq", NONE) for k in sorted(site.bound))
        label = _label((phrase, *index), f"{pairs[0][0]}:{pairs[0][1]}")
        edits.append(Edit(label, partial(_snapped, phrase, index, pairs[0], nones)))
    return edits


snap.traced = True  # the driver passes the guarantee's horizon
