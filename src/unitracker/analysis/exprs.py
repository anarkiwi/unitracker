"""The ``exprs`` report: each song's modulators, proofs, general forms and token counts."""

import dataclasses
from pathlib import Path

import numpy as np
from pysidtracker import find_format

from unitracker import document
from unitracker.cache import CACHE
from unitracker.optimise.refs import references
from unitracker.playroutine.records import EXPR
from unitracker.lift import backends, verify
from unitracker.modfit import grammar as G
from unitracker.modfit.convert import analyse, groups
from unitracker.modfit.exprs import Result
from unitracker.modfit.inherit import inherit
from unitracker.modfit.phase import unproved_functions
from unitracker.player.modulation import declared

KEYS = ("freq", "pw", "cut")

TICKS = {"goattracker2": 2 * 3840, "defmon": 95 + 2 * 1152 + 100, "sidwizard": 2304 * 6 + 600}


@dataclasses.dataclass
class Song:
    """A tune: its bytes and the lifter's song with its native modulations."""

    path: Path
    ir: dict
    raw: bytes

    @property
    def player(self) -> str:
        """The playroutine name."""
        return self.ir["playroutine"]["name"]


def load(path: Path) -> Song:
    """A tune read for analysis, lifted with its native modulations."""
    raw = Path(path).read_bytes()
    return Song(Path(path), backends.lift(raw, None, expressions=False), raw)


def native_grid(song: Song, ticks: int) -> np.ndarray:
    """The native player's register grid of a tune."""
    return np.asarray(find_format(song.raw).play(song.raw).render_grid(ticks), dtype=np.uint8)


def start_lines(r: Result, tag: str) -> list:
    """The expressions and proof of one start."""
    d = r.derived
    o = d.order
    kind = {"time": "elapsed firings", "turn": "turns"}[o.kind]
    head = f"{tag} phase ({kind}, {d.states} control states, prefix {o.prefix}, period {o.period})"
    lines = [f"{head}: φ' = φ + {G.text(d.phase)}", f"{tag} value: v' = {G.text(d.value)}"]
    if d.closed is not None:
        start = "any" if r.start_value is None else G.text(r.start_value)
        lines.append(f"{tag} closed form (start value {start}): v = {G.text(d.closed)}")
    else:
        why = "the start value is the incoming one" if r.v0 is None else "a value update reads v"
        lines.append(f"{tag} closed form: none ({why})")
    lines.append(f"{tag} proof: " + "; ".join(p[0] for p in d.proof) + ": proved")
    inv = d.invariant
    if inv is not None:
        held = " and ".join(inv.glob + inv.unit) or "true"
        what = "proved" if inv.ok else f"NOT proved: {inv.why}"
        lines.append(f"{tag} invariant: under {held} the recurrence is {what}")
    return lines


def locations(ir: dict) -> dict:
    """``{expression text: [where the song uses it]}``: a record's field (``type index field``)
    or an event's command (``phrase[event]``)."""
    out = {}
    for place, target, index in references(ir):
        if target == EXPR and index in ir["exprs"]:
            where = (
                " ".join(map(str, place[1:])) if place[0] == "record" else f"{place[1]}[{place[2]}]"
            )
            out.setdefault(str(ir["exprs"][index]), []).append(where)
    return out


def group_lines(conv, an, key: tuple, members: list, places: dict) -> list:
    """One unit and label: its occurrences, distinct starts, texts, places and general form."""
    # pylint: disable=too-many-locals
    unit, label = an.unit(key[0]), key[1]
    occs = [conv.obs["occurrences"][i] for i in members]
    starts = list(dict.fromkeys(o.key for o in occs))
    results = [Result(conv.found[s][0], s[1], s[2], start_value=conv.found[s][1]) for s in starts]
    live = [r for r in results if not r.derived.identity]
    cells = ", ".join(f"{c} {v}" for c, v in zip(unit.get("label") or [], label))
    lines = [f"### {unit['name']} [{cells}] (cell {unit['cell']})"]
    chip = unit.get("chip")
    if chip:
        store = ", ".join(f"{c} = {e}" for c, e in chip["store"].items())
        lines.append(f"    chip units: {chip['width']} bits, v = {chip['value']}; store {store}")
    quiet = len(results) - len(live)
    lines.append(f"    occurrences {len(occs)}, distinct starts {len(live)}"
                 + (f" (and {quiet} that do not modulate)" if quiet else ""))  # fmt: skip
    for i, r in enumerate(live):
        lines += start_lines(r, f"    start {i + 1}" if len(live) > 1 else "   ")
    texts = {}
    for i in members:
        if conv.texts[i] is not None:
            texts[conv.texts[i]] = texts.get(conv.texts[i], 0) + 1
    for text, n in texts.items():
        where = ", ".join(places.get(text, [])) or "native rows"
        lines.append(f"    written ({n} occurrences): {text}  at {where}")
    if len(texts) > 1 and live:
        inh = inherit(an, unit, live)
        if inh.update is None:
            lines.append(f"    general form of {', '.join(inh.cells)}: none: {inh.reason}")
        else:
            what = "inputs" if inh.kind == "input" else "delay gate over"
            lines.append(
                f"    general form ({what} {', '.join(inh.cells)}): {G.text(inh.update[1])}"
            )
            lines.append(f"    general form proof: {inh.proof}")
    return lines


def song_lines(song: Song, workers) -> tuple:
    """``(report lines, song text)`` of one song."""
    conv = analyse(song.ir, CACHE, workers)
    an = conv.analysis
    text = document.write(conv.out)
    ticks = TICKS[song.player]
    verdict = verify.compare(text, native_grid(song, ticks))
    lines = [f"## {song.path.stem} ({song.player})"]
    occs = conv.obs["occurrences"]
    lines.append(f"  occurrences {len(occs)} over {conv.ticks} ticks (to the state's recurrence "
                 f"and one period more); expressions {len(conv.out['exprs'])}")  # fmt: skip
    lines.append("  the rewritten song plays the native song's register grid exactly over them")
    lines.append(
        f"  its generic document on unitracker-player over {ticks} ticks: {verdict.line()}"
    )
    unproved = unproved_functions(song.ir, declared(song.ir["playroutine"]))
    if unproved:
        lines.append(f"  named-table functions NOT equal: {unproved}")
    places = locations(conv.out)
    for key, members in groups(occs).items():
        if any(conv.texts[i] is not None for i in members):
            lines += [""] + group_lines(conv, an, key, members, places)
    return lines + [""], text


def report(paths, workers=None) -> list:
    """The ``exprs`` report of tunes, with the token count of each one's generic document."""
    lines = [
        "# Modulators as phase and value expressions",
        "",
        "Per firing, on the state before it: v' = f(v, φ) and φ' = φ + 1. φ starts at 0, v at",
        "the cell's value when the modulator starts; a closed form g is the value after the",
        "firing at φ. N is the voice's sounding note in format numbering, F(k) the player's",
        "frequency of note k, W the SID-Wizard speed table (exptab below 107, then frequencies).",
        "Proofs are z3 over 48-bit integers. Values a start carries from earlier playback are",
        "constants of its expression; a general form proves one expression for every value.",
        "",
        "## Tokens",
        "",
    ]
    body, total = [], 0
    for path in paths:
        song = load(Path(path))
        more, text = song_lines(song, workers)
        body += more
        count = G.tokens(text)
        total += count
        lines.append(f"  {song.path.stem}: {count}")
    return lines + [f"  total: {total}", ""] + body
