"""The ``exprs`` report: each song's modulators, proofs, general forms and token counts."""

import dataclasses
from pathlib import Path

import numpy as np
import yaml

from unitracker.format import songio
from unitracker.lift import backends
from unitracker.modfit import grammar as G
from unitracker.modfit.convert import CACHE, analyse, groups
from unitracker.modfit.exprs import Result
from unitracker.modfit.inherit import inherit
from unitracker.modfit.phase import unproved_functions
from unitracker.player.modulation import declared
from unitracker.player.vm import SongPlayer

KEYS = ("freq", "pw", "cut")

TICKS = {"goattracker2": 2 * 3840, "defmon": 95 + 2 * 1152 + 100, "sidwizard": 2304 * 6 + 600}


@dataclasses.dataclass
class Song:
    """A tune: its bytes and its internal form lifted with its native modulations."""

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
    return np.asarray(backends.native_player(song.raw).render_grid(ticks), dtype=np.uint8)


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


def locations(doc: dict) -> dict:
    """``{expression text: [where the document writes it]}``."""
    out = {}

    def rows(where: str, value):
        if isinstance(value, dict):
            for key in KEYS:
                if isinstance(value.get(key), (str, int)):
                    out.setdefault(str(value[key]), []).append(f"{where} {key}")
            for field, sub in value.items():
                if isinstance(sub, list):
                    rows(f"{where} {field}", sub)
        elif isinstance(value, list):
            for k, row in enumerate(value):
                if isinstance(row, dict):
                    rows(f"{where} row {k}", row)

    for item in doc["song"]:
        for kind, defs in (item.get("def") or {}).items():
            for name, value in (defs or {}).items():
                if kind == "x":
                    out.setdefault(str(value), []).append(f"!freq:{name}")
                elif kind != "phrase" and isinstance(name, str):
                    rows(name, value)
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
    text = songio.dumps(conv.out)
    ticks = TICKS[song.player]
    exact = SongPlayer(songio.loads(text)).mismatch(native_grid(song, ticks)) is None
    lines = [f"## {song.path.stem} ({song.player})"]
    occs = conv.obs["occurrences"]
    lines.append(f"  occurrences {len(occs)} over {conv.ticks} ticks (to the state's recurrence "
                 f"and one period more); expressions {len(conv.out['exprs'])}")  # fmt: skip
    lines.append("  the rewritten song plays the native song's register grid exactly over them")
    same = "equal to" if exact else "DIFFERENT from"
    lines.append(
        f"  read back from its v{songio.VERSION} text, {ticks} ticks are {same} the native player's"
    )
    unproved = unproved_functions(song.ir, declared(song.ir["playroutine"]))
    if unproved:
        lines.append(f"  named-table functions NOT equal: {unproved}")
    places = locations(yaml.safe_load(text))
    for key, members in groups(occs).items():
        if any(conv.texts[i] is not None for i in members):
            lines += [""] + group_lines(conv, an, key, members, places)
    return lines + [""], text


def report(paths, before: Path | None = None, after: Path | None = None, workers=None) -> list:
    """The ``exprs`` report of tunes, with token counts of their v1.3, v1.4 and v1.5 documents."""
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
    ]
    tokens = []
    for path in paths:
        song = load(Path(path))
        more, text = song_lines(song, workers)
        lines += more
        tokens.append((song.path.stem, text))
    return lines + ["## Tokens", ""] + token_lines(tokens, before, after)


def token_lines(songs: list, before: Path | None, after: Path | None) -> list:
    """Tokens of each song in v1.3, v1.4 and v1.5."""
    lines = ["  song: v1.3 / v1.4 / v1.5"]
    totals = [0, 0, 0]
    for stem, text in songs:
        counts = [_tokens_of(before / f"{stem}.yaml") if before else None,
                  _tokens_of(after / f"{stem}.yaml") if after else None,
                  G.tokens(text)]  # fmt: skip
        for i, n in enumerate(counts):
            totals[i] += n or 0
        lines.append(f"  {stem}: " + " / ".join("-" if n is None else str(n) for n in counts))
    lines.append("  total: " + " / ".join(str(n) for n in totals))
    return lines


def _tokens_of(path: Path) -> int | None:
    """Tokens of a file, or None when it is missing."""
    return G.tokens(path.read_text(encoding="utf-8")) if path.exists() else None
