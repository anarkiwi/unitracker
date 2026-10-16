"""The generic accumulator report: each keyword and record row kind as one factorised event.

A row gives the precise register role, the step and its basis, the hit, the respond and the
gate (a timer's signature, or the command's lifetime) with the proof status. Rows are then
grouped by role, respond and temporal kind across the playroutines.
"""

import dataclasses

from tqdm import tqdm

from unitracker.modfit import resolve, specs
from unitracker.modfit.compose import keywords
from unitracker.modfit.factor import Factorer, basis, direct, keyword_factors
from unitracker.modfit.fit import src
from unitracker.modfit.keywords import NONE, role_text, signer
from unitracker.modfit.compose import Merge
from unitracker.modfit.timer import Timers, closed, grouped, signature, spans, timer_lines
from unitracker.modfit.timer import vibrato_lines
from unitracker.modfit.bounce import bounce_lines

PLAYERS = ("goattracker2", "defmon", "sidwizard")
HEAD = [
    "player",
    "keyword/row",
    "cell",
    "role",
    "step",
    "basis",
    "hit",
    "respond",
    "gate",
    "status",
]


@dataclasses.dataclass
class Row:
    """One factorised event."""

    # pylint: disable=too-many-instance-attributes

    player: str
    label: str
    cell: str
    role: str
    step: str
    basis: str
    hit: str
    respond: str
    kind: str
    gate: str
    temporal: str
    status: str

    def cells(self) -> list:
        """The table cells."""
        cells = [self.player, self.label, self.cell, self.role, self.step, self.basis, self.hit,
                 self.respond, self.gate, self.status]  # fmt: skip
        return [c.replace("|", "\\|") for c in cells]


class Report:
    """Rows of one library playroutine."""

    def __init__(self, name: str):
        self.name = name
        self.signer = signer(name)
        self.comp = self.signer.comp
        self.timers = Timers(name)
        self.found = self.timers.timers()
        self.summaries = {}

    def summary(self, cell: str) -> str:
        """A timer's duration to its first fire as a closed form, else its first test's."""
        if cell not in self.summaries:
            t = self.found[cell]
            orbit = self.timers.orbit(t, t.lists[0])
            fired = {p: d for p in orbit.domain() if (d := orbit.duration(p)) is not None}
            what = src(orbit.bound) if orbit.bound is not None else f"start {cell}"
            if fired:
                text = f"counts {closed(fired)} ticks for {what} = p in {spans(fired)}"
            else:
                sigs = {p: signature(*orbit.truths(t.tests[0], p)) for p in orbit.domain()}
                text = "; ".join(
                    f"`{src(t.tests[0])}` {kind} {' '.join(forms)} for {what} = p in {where}"
                    for kind, where, forms in grouped(sigs)
                )
            self.summaries[cell] = f"timer {cell} ({t.step:+d}): {text}"
        return self.summaries[cell]

    def gate(self, factor, lifetime: str) -> tuple:
        """``(text, temporal kind)``: the timers the gate reads, else ``lifetime``."""
        read = set().union(*(direct(g) for g in factor.gate)) & set(self.found)
        if read:
            return "; ".join(self.summary(c) for c in sorted(read)), "timer"
        return lifetime, lifetime

    def row(self, label: str, cell: str, factor, lifetime: str) -> Row:
        """The row of one factorised gate group."""
        gate, temporal = self.gate(factor, lifetime)
        kind = ("+".join(factor.kinds) or "accumulate") if factor.proved else "-"
        respond = f"{kind}: {factor.respond}" if factor.hit != "-" else kind
        step = factor.texts.get("step") or (src(factor.step) if factor.step is not None else NONE)
        return Row(
            self.name,
            label,
            cell,
            role_text(self.signer.cell_role(cell)),
            step,
            basis(factor.step, self.signer.notes) if factor.step is not None else NONE,
            factor.hit,
            respond,
            kind,
            gate,
            temporal,
            factor.status,
        )

    def lifetime(self, code: int, when) -> str:
        """``until the next note`` when a note row resets an armed cell, else ``until replaced``."""
        armed = [e[3] for e in self.signer.keyword(code, when)["entries"] if e[1] == "ARM"]
        base = self.comp.baseline("note")
        if any(base.get(c, c) != c for c in armed):
            return "until the next note"
        return "until replaced"

    def keyword_rows(self, code: int, kw: str, when) -> list:
        """Rows of a keyword: its per-tick effect's gate groups, or its setup."""
        label = f"!{kw} ({code}{', ' + when if when else ''})"
        factors = keyword_factors(self.comp, code, when)
        if factors:
            life = self.lifetime(code, when)
            rows = [self.row(label, cell, f, life) for cell, f in factors]
            return [r for r in rows if r.role != NONE]
        entries = [e for e in self.signer.keyword(code, when)["entries"] if e[0] != NONE]
        if not entries:
            return []
        role = "; ".join(dict.fromkeys(e[0] for e in entries))
        kinds = "+".join(sorted({e[1] for e in entries}))
        cells = ",".join(sorted({e[3] for e in entries}))
        return [Row(self.name, label, cells, role, NONE, NONE, NONE, kinds, kinds, "setup",
                    "setup", NONE)]  # fmt: skip

    def record_rows(self, path: str, cell: str) -> list:
        """Rows of a modulation's action list: one per gate group, a record row kind."""
        fz = Factorer(Merge(self.signer.pr))
        rows = [
            self.row(f"{path} [{' and '.join(src(g) for g in f.gate) or 'always'}]", cell, f,
                     "row duration")  # fmt: skip
            for f in fz.unit(resolve(fz.form.pr, path), cell)
        ]
        return [r for r in rows if r.role != NONE]

    def rows(self) -> list:
        """Every keyword's and modulation's rows."""
        out = []
        for code, kw, when in tqdm(keywords(self.signer.pr), desc=f"{self.name} generic"):
            out += self.keyword_rows(code, kw, when)
        for unit in specs()["fits"]:
            if unit["playroutine"] == self.name:
                out += self.record_rows(unit["path"], unit["cell"])
        return out


def table(rows: list) -> list:
    """Markdown lines of the rows."""
    return [" | ".join(HEAD), " | ".join("---" for _ in HEAD)] + [
        " | ".join(r.cells()) for r in rows
    ]


def grouping(rows: list) -> list:
    """Lines: the rows of each (role, respond kind, temporal kind), across playroutines."""
    groups = {}
    for r in rows:
        groups.setdefault((r.role, r.kind, r.temporal), []).append(f"{r.player}:{r.label}")
    lines = ["role | respond | temporal | players | members", "--- | --- | --- | --- | ---"]
    for (role, kind, temporal), members in sorted(groups.items(), key=lambda g: (-len(g[1]), g[0])):
        players = len({m.split(":")[0] for m in members})
        lines.append(
            f"{role} | {kind} | {temporal} | {players} | {', '.join(dict.fromkeys(members))}"
        )
    return lines


def report(names=PLAYERS, ticks: int = 600) -> list:
    """The generic report: rows, grouping, timers, vibrato and bounce orbits."""
    rows = [r for name in names for r in Report(name).rows()]
    lines = ["# Generic accumulator events", ""] + table(rows)
    lines += ["", "# Grouping by role, respond and temporal kind", ""] + grouping(rows)
    lines += ["", "# Timers", ""] + [x for name in names for x in timer_lines(name)]
    bounces = [tuple(u.values()) for u in specs()["bounces"] if u["playroutine"] in names]
    lines += ["", "# Ping-pong orbits", ""] + vibrato_lines(names, ticks) + bounce_lines(bounces)
    return lines
