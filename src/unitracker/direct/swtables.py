"""SID-Wizard's WF, pulse and filter tables as the generic document's programs, for
:class:`unitracker.direct.sidwizard.Transcriber`: a row per table read, the player's jumps,
sweeps, chords and ARPSCNT pacing, each program from a position the player is at."""

from pysidwizard.constants import INST_WF_TABLE_POS
from unitracker_grammar import code
from unitracker_grammar.document import Call, Expr, Program, Row, Signed
from unitracker_grammar.expr import canonical

from unitracker.direct.goattracker2 import note_name
from unitracker.direct.swtrace import TraceError, _col


class TranscribeError(TraceError):
    """The tune uses something this transcription does not spell."""


def _unnoted(row: Row) -> Row:
    """WF ``row`` on a voice yet to play a note (DPITCH 0): its relative arp as the absolute
    note it reaches, a relative arp down carrying nothing."""
    keys = dict(row.keys)
    if isinstance(keys.get("note"), Signed):
        keys["note"] = note_name((keys["note"] & 0x7F) - 1)
    if "dt.freq" in keys and keys["dt.freq"].args[2] == 2:
        keys["dt.freq"] = Call("det", keys["dt.freq"].args[:2] + (0, 0))
    return Row(keys)


def _swept_first(table: bytes) -> bool:
    """Whether a pulse or filter table starts with a sweep, whose count is left from before."""
    return bool(table) and table[0] < 0x80


def _signed(byte: int) -> int:
    return byte - 0x100 if byte >= 0x80 else byte


def _rows3(table: bytes):
    return (table[i : i + 3] for i in range(0, len(table), 3))


class Tables:  # pylint: disable=too-few-public-methods
    """The table programs: methods of the transcriber, which keeps the reference player
    ``ref``, its build ``feats``, the program stores and the templates it calls."""

    swm = ref = feats = None
    programs: dict = {}
    used: list = []
    routines: dict = {}
    mirror = pwkb = fkb = False
    walk = "cut"

    def call(self, template: str, **params) -> Call:
        """The call of ``template`` with ``params``, its arguments in its signature's order."""
        if template not in self.used:
            self.used.append(template)
        return Call(template, tuple(params[k] for k in code.params(self.routines[template])))

    def reload(self, ins) -> int:
        """ARPSPED's reload of instrument ``ins``: the ticks after its first a WF row lasts."""
        return ins.arp_speed & 0x3F if self.feats.arpspeedsupp else 0

    def _named(self, kind: str, prefix: str, program: Program) -> str:
        store = self.programs[kind]
        for name, held in store.items():
            if held == program:
                return name
        name = f"{prefix}{len(store)}"
        store[name] = program
        return name

    def _det(self, keys: dict, detune, carry: int, arp: int = 0) -> dict:
        """``keys`` with the row's WRPITCH binding on a detune build."""
        if not self.feats.detunesupport:
            return keys
        keep = detune is None
        call = self.call("det", f=int(not keep), d=0 if keep else detune, c=carry, b=arp)
        return keys | {"dt.freq": call}

    def skipped(self, ins, ticks: int) -> list:
        """The rows of ``ticks`` WF ticks ARPSCNT skips, their carry the pulse walk's."""
        if not ticks:
            return []
        if not self.feats.detunesupport:
            return [Row({"hold": ticks})]
        if self.feats.pwkeybtrack:
            carry = 2 if self.mirror else 0
        elif not ins.pw_table:
            carry = 1
        else:
            raise TranscribeError("WF rows held over ticks with a pulse table, its carry")
        return [Row(self._det({"hold": ticks} if ticks > 1 else {}, None, 3, carry))]

    def _rest(self, rows: list, ins, reload: int, carry: int) -> tuple:
        """``rows`` and the WF walk resting where it ends: re-read at its pace, each read's
        carry ``carry``, ARPSCNT's skipped ticks between."""
        if not self.feats.detunesupport:
            return rows, None
        rest = [Row(self._det({}, None, carry))] + self.skipped(ins, reload)
        return rows + rest, len(rows) if reload else None

    def wave_rows(self, image: bytes, pos: int, ins, limit: int = 1 << 12, **at_start) -> tuple:
        """``(rows, loop)`` of instrument ``ins``'s WFARPTB from ``pos``, row by row as the
        player reads them: each lasts its arp speed or hold, jumps taken on the tick; a chord
        row plays its chord from its start (or ``chord`` at first)."""
        # pylint: disable=too-many-locals
        reload = at_start.get("reload", self.reload(ins))
        number, cpos = at_start.get("chord", (ins.default_chord, None))
        rows, at, ticks = [], {}, 0
        while ticks < limit:
            for _ in range(4):
                byte = _col(image, pos, 0xFF)
                if byte == 0xFF:
                    return self._rest(rows, ins, reload, 1)
                if byte != 0xFE:
                    break
                target = _col(image, pos + 1)
                if target >= 0x80 or target == pos:
                    return self._rest(rows, ins, reload, 1)
                if target >= len(image):
                    return self._rest(rows, ins, reload, 0)
                pos = target
            else:
                raise TranscribeError("a WF jump chain")
            if pos in at:
                return rows, at[pos]
            at[pos] = len(rows)
            keys, hold = self._wave_row(image, pos, reload)
            first, cpos = cpos, None
            if self.feats.chordsupport and _col(image, pos + 1) == 0x7F:
                played, looped = self._chord(ins, (keys, _col(image, pos + 2)), hold, number, first)
                if looped is not None:
                    return rows + played, len(rows) + looped
                rows += played
                ticks += hold * len(played)
                pos += 3
                continue
            rows += [Row(keys)] + self.skipped(ins, hold - 1)
            ticks += hold
            pos += 3
        return rows, None

    def _chord(self, ins, row: tuple, hold: int, number: int, pos=None) -> tuple:
        """``(rows, loop)`` of a chord row playing chord ``number``: a WF read per pitch with
        the row's waveform and detune (PLYCHRD's, never kept), from ``pos`` (else the chord's
        start) to its $7E (on to the next row in the tick) or its $7F (its first pitch again:
        ``loop`` the row it loops to)."""
        # pylint: disable=protected-access
        starts, table = self.ref._chord_starts, self.ref._chord_table
        if not 1 <= number <= len(starts) or _col(table, starts[number - 1], 0x7F) == 0x7F:
            raise TranscribeError(f"chord {number}, past the chord table or empty")
        start = starts[number - 1]
        base = {k: v for k, v in row[0].items() if k == "ctrl"}

        def played(at: int) -> tuple:
            rows = []
            while at < len(table) and table[at] not in (0x7E, 0x7F):
                pitch = base | {"note": Signed(table[at]), "wave.freq": self.call("tone")}
                rows += [Row(self._det(pitch, row[1], 2, table[at]))] + self.skipped(ins, hold - 1)
                at += 1
            return rows, at < len(table) and table[at] == 0x7F

        rows, looped = played(start if pos is None or pos >= len(table) else pos)
        if not looped:
            return rows, None
        if pos is None or pos == start:
            return rows, 0
        cycle, _ = played(start)
        return rows + cycle, len(rows)

    def _wave_row(self, image: bytes, pos: int, reload: int) -> tuple:
        """``(keys, ticks)`` of the WF row at ``pos``: its waveform (or hold), its arp as the
        note, its detune and WRPITCH's carry."""
        feats, byte = self.feats, image[pos]
        keys, hold = {}, reload + 1
        if byte < 0x10 and feats.arpspeedsupp:
            hold = byte + 1
        else:
            keys["ctrl"] = byte
        arp, carry = _col(image, pos + 1, None), 0
        if arp is not None and not (arp == 0x80 and feats.wfarp_nop_supp):
            if 0x80 <= arp < 0xE0:
                keys["note"] = note_name((arp & 0x7F) - 1)
            else:
                keys["note"], carry = Signed(arp - 0x100 if arp >= 0xE0 else arp), 2
            keys["wave.freq"] = self.call("tone")
        elif arp is not None:
            carry = 1
        detune = _col(image, pos + 2, 0xFF)
        return self._det(keys, None if detune == 0xFF else detune, carry, arp or 0), hold

    def wave(self, ins, chord=None, kept=(None, None, False)) -> str:
        """The ``ctrl`` name of instrument ``ins``'s WF program from STRTSND's tick: its first
        waveform (control $09 without the first-frame switch), SETFLTP's routing; with the
        reset switches, the pulse or filter walk ``kept`` resumed instead of restarted (the
        filter's routed, or not, by SETFLTP)."""
        # pylint: disable=too-many-locals
        feats, (pulse, filt, routed) = self.feats, kept
        table = ins.filter_table
        first = {"route_on" if table and table[0] != 0xFF else "route_off": 0}
        if filt is not None and not routed:
            first = {}
        runs = []
        if pulse is not None:
            runs.append(Row({"hold": 0, "run": self.pulse(ins, *pulse)}))
        if filt:
            runs.append(Row({"hold": 0, "run": self.filter(ins, filt[0], filt[1])}))
        lead = []
        if (
            not feats.frame1switch
            or ins.control & 0x08
            and self.ref.image.quirks.first_frame_fixed_waveform
        ):
            first["ctrl"] = 9
        elif ins.control & 0x08:
            first["ctrl"] = ins.effective_first_waveform
        elif feats.hardrestypes and not ins.control & 0x03:
            raise TranscribeError("a first frame that keeps a waveform no hard restart gated off")
        else:
            first["gate"] = 0
            lead = [Row({"hold": 0, "gate": 1})]
        if ins.wf_table:
            image = self.ref._image(ins)  # pylint: disable=protected-access
            number = ins.default_chord if chord is None else chord
            rows, loop = self.wave_rows(image, INST_WF_TABLE_POS, ins, chord=(number, None))
            if lead and rows[0].get("ctrl") is None:
                raise TranscribeError("a first frame that keeps a waveform before a hold row")
        else:
            (rows, loop), lead = self._rest([], ins, 0, 1), []
        skip = len(runs) + 1 + len(lead)
        program = Program([*runs, Row(first), *lead, *rows], None if loop is None else loop + skip)
        return self._named("ctrl", "c", program)

    def _swept(
        self, image: bytes, pos: int, cell: str, rows_of: dict, count=None, lead=True
    ) -> tuple:
        """``(rows, loop)`` of a pulse or filter program from ``pos``; ``rows_of`` gives the
        keys of a ``set`` row and of a ``sweep``'s ticks, its ``advance`` tick, a ``jump`` to a
        sweep and the ``rest`` a tracked table keeps writing. Resumed with sweep ``count``, it
        leaves STRTSND's tick to the voice's bindings."""
        # pylint: disable=too-many-arguments,too-many-positional-arguments
        rows, at = [Row(rows_of["started"] if count is None else rows_of["resumed"])], {}
        rows = rows if lead else []
        first = True
        while True:
            if pos >= len(image) or image[pos] == 0xFF:
                return rows + rows_of["rest"], None
            if image[pos] == 0xFE:
                target = _col(image, pos + 1)
                if target == pos or target >= len(image):
                    return rows + rows_of["rest"], None
                if image[target] in (0xFE, 0xFF):
                    raise TranscribeError(f"a {cell} jump to a jump or an end")
                if image[target] < 0x80:
                    rows.append(Row(rows_of["jump"]))
                pos = target
            if pos in at:
                return rows, at[pos]
            byte = image[pos]
            if first and count is not None and not byte & 0x80:
                # PWSWEEP steps until its count equals the row's; FILTPRG until it reaches it.
                left = (byte - count) & 0xFF if cell == "pw" else byte - count
                rows += self._sweep(rows_of, pos, left)
                first = False
                pos += 3
                continue
            at[pos] = len(rows)
            if byte & 0x80:
                rows.append(Row(rows_of["set"](pos)))
            elif first:
                raise TranscribeError(f"a {cell} table that starts with a sweep")
            else:
                rows += self._sweep(rows_of, pos, byte)
            first = False
            pos += 3

    @staticmethod
    def _sweep(rows_of: dict, pos: int, ticks: int) -> list:
        """A sweep row's ``ticks`` steps, then its advance tick."""
        steps = [Row({"hold": ticks} | rows_of["sweep"](pos))] if ticks > 0 else []
        return steps + [Row(rows_of["advance"](pos, ticks > 0))]

    def pulse(self, ins, pos=None, count=None, lead=True) -> str:
        """The ``pw`` name of instrument ``ins``'s pulse program, from the tick after
        STRTSND's, or resumed from ``pos`` at sweep ``count``."""
        image = self.ref._image(ins)  # pylint: disable=protected-access

        def width(p):
            return ((image[p] & 0x7F) << 8 | _col(image, p + 1)) & (0x7FFF if self.pwkb else 0xFFF)

        if self.pwkb:
            rows_of = {
                "set": lambda p: self._pulsed("st", w=width(p), j=_col(image, p + 2)),
                "sweep": lambda p: self._pulsed("sw", d=_signed(_col(image, p + 1))),
                "advance": lambda p, _: self._pulsed("id", j=_col(image, p + 2)),
                "jump": self._pulsed("wr"),
                "rest": [Row(self._pulsed("wr"))],
                "resumed": {"pw": "none"} | ({"pm.freq": "none"} if self.mirror else {}),
                "started": {"pw": "none"} | ({"pm.freq": "none"} if self.mirror else {}),
            }
        else:
            rows_of = {
                "set": lambda p: {"pw": width(p)},
                "sweep": lambda p: {"pw": Expr(canonical(f"v + {_signed(_col(image, p + 1))}"))},
                "advance": lambda p, swept: {"pw": "none"} if swept else {},
                "jump": {},
                "rest": [],
                "resumed": {"pw": "none"},
                "started": {"pw": "none"},
            }
        start = INST_WF_TABLE_POS + len(ins.wf_table) + 1 if pos is None else pos
        rows, loop = self._swept(image, start, "pw", rows_of, count, lead)
        return self._named("pw", "pw", Program(rows, loop))

    def _pulsed(self, kind: str, **params) -> dict:
        """A tracked pulse row's binding of template ``p`` + ``kind``, and its mirror's."""
        out = {"pw": self.call("p" + kind, **params)}
        return out | ({"pm.freq": self.call("m" + kind, **params)} if self.mirror else {})

    def filter(self, ins, pos=None, count=None, handover=None, lead=True) -> str:
        """The ``filter`` name of instrument ``ins``'s filter program, or resumed from ``pos``
        at sweep ``count``; taken on a tick the previous voice's walk stepped, from the state
        that step ``handover`` left."""
        # pylint: disable=too-many-locals
        image = self.ref._image(ins)  # pylint: disable=protected-access
        scale = 1 if self.feats.finefiltsweep else 8

        def bands(p):
            return {"res": image[p] & 0x0F, "mode": image[p] & 0x70}

        def fladvan(p, call, **params):
            # From 1.4 a $8x third column is FSWITCH, the routing, and no keyboard track.
            byte = _col(image, p + 2)
            if fswitch and 0x80 <= byte < 0x90:
                return {self.walk: self.call(call, **params, j=0), "route": byte & 0x0F}
            return {self.walk: self.call(call, **params, j=byte)}

        fswitch = self.feats.filtkbtrack and self.ref.image.quirks.kbtrack_fswitch

        if self.fkb:
            rows_of = {
                "set": lambda p: bands(p) | fladvan(p, "fst", c=_col(image, p + 1)),
                "sweep": lambda p: {self.walk: self.call("fsw", d=_signed(_col(image, p + 1)))},
                "advance": lambda p, _: fladvan(p, "fid"),
                "jump": {self.walk: self.call("frs")},
                "rest": [Row({self.walk: self.call("frs")})],
                "resumed": {self.walk: self.call("frs")},
                "started": {self.walk: self.call("frs")},
            }
        else:
            rows_of = {
                "set": lambda p: bands(p) | {"cut": _col(image, p + 1) << 3},
                "sweep": lambda p: {
                    "cut": Expr(canonical(f"v + {scale * _signed(_col(image, p + 1))}"))
                },
                "advance": lambda p, swept: {"cut": "none"} if swept else {},
                "jump": {},
                "rest": [],
                "resumed": {"cut": "none"},
                "started": {"cut": "none"},
            }
        if handover is not None:
            high, low, res, mode, track = handover
            cut = {self.walk: self.call("fho", c=high, l=low & 7, j=track)} if self.fkb else {}
            if not self.fkb:
                cut = {"cut": high << 3 | low & 7}
            rows_of["started"] = {"res": res, "mode": mode} | cut
        if pos is None:
            pos = INST_WF_TABLE_POS + len(ins.wf_table) + 1 + len(ins.pw_table) + 1
        rows, loop = self._swept(image, pos, "cut", rows_of, count, lead)
        return self._named("filter", "f", Program(rows, loop))

    def resume(self, ins, wave: tuple, pulse=None, filt=None, skip=True, **at) -> str:
        """The ``ctrl`` name of a program that skips a tick of the tables (a legato's TICK_2, a
        staccato hard restart; not ``skip``: a chord FX) and resumes them where the player left
        them: ``wave`` the WF position, ARPSCNT, chord and its position, ``pulse`` and ``filt``
        positions and sweep counts."""
        # pylint: disable=too-many-locals
        reload = at.get("reload")
        lead = []
        if pulse is not None:
            lead.append(Row({"hold": 0, "run": self.pulse(ins, *pulse, lead=skip)}))
        if filt is not None:
            lead.append(Row({"hold": 0, "run": self.filter(ins, *filt, lead=skip)}))
        pos, counter, chord, cpos = wave
        held = counter if self.feats.arpspeedsupp and counter < 0x80 else 0
        lead += ([Row({})] if skip else []) + self.skipped(ins, held)
        image = self.ref._image(ins)  # pylint: disable=protected-access
        reload = self.reload(ins) if reload is None else reload
        rows, loop = self.wave_rows(image, pos, ins, chord=(chord, cpos), reload=reload)
        program = Program(lead + rows, None if loop is None else loop + len(lead))
        return self._named("ctrl", "c", program)

    def rechord(self, rec) -> str:
        """The ``ctrl`` name of the WF walk a chord FX leaves: from STRTSND on a note (the FX
        deferred past it), else from where it is, the chord its own."""
        if rec.chord is None:
            raise TranscribeError("a chord FX before any instrument")
        pos, counter, chord, ins = rec.chord
        if rec.newnote:
            return self.wave(ins, chord)
        return self.resume(ins, (pos, counter, chord, None), skip=False)
