"""Leaves the selector emits, made minimal by jennings' superoptimiser: a straight-line leaf
becomes a jennings problem on its template's spec (live-outs its outputs, registers known at
entry as constants) and the proved minimal sequence, if shorter, replaces it."""

from jennings.opcodes import MODE_LEN, OPCODES
from jennings.superopt.search import Problem, search
from unitracker_grammar.code import Imm, Label, Mem, op

from unitracker.optimiser.code6502.semantics import CELL, ENTRY, layout

_REGISTERS = {"a": "A", "x": "X", "y": "Y"}


class Leaves:  # pylint: disable=too-few-public-methods
    """The superoptimiser for the leaves of routines of ``shape``; ``wide`` the parameters
    read by both bytes, ``max_length`` the longest sequence tried."""

    def __init__(self, shape, wide=(), backend="auto", max_length=5):
        self.shape, self.wide = shape, frozenset(wide)
        self.backend, self.max_length = backend, max_length
        self.cache, self.searches = {}, []

    def improve(self, statements, outputs, machine):
        """A shorter equivalent of the leaf ``statements`` (no labels, no terminator) storing
        ``outputs``, given what ``machine`` knows at its entry; else None."""
        if any(isinstance(s, Label) for s in statements) or len(statements) < 2:
            return None
        known = {_REGISTERS[r]: t[1] for r, t in machine.reg.items() if t and t[0] == "BC"}
        if machine.carry is not None:
            known["C"] = int(machine.carry)
        key = (tuple(statements), tuple(outputs), tuple(sorted(known.items())))
        if key not in self.cache:
            self.cache[key] = self._search(statements, outputs, known)
        return self.cache[key]

    def _search(self, statements, outputs, known):
        machine, spec = layout(self.shape)
        bag = dict.fromkeys(self.shape.params, 0)
        routine = machine.compile("leaf", tuple(statements), bag, CELL)
        immediates, pc = {}, routine.start
        for s in statements:
            if isinstance(s.operand, Imm) and isinstance(s.operand.value, str):
                immediates[pc] = (f"#{s.operand.value}", s.operand.byte == ">")
            pc += MODE_LEN[OPCODES[machine.memory[pc]][1]]
        entry = {k: v for k, v in ENTRY.items() if k in known or k in ("D", "I")}
        entry.update(known)
        spec = type(spec)(spec.vars, spec.tables, spec.params, entry, tuple(outputs))
        code = bytes(machine.memory[routine.start : routine.end])
        found = search(
            Problem(spec, code, routine.start, immediates),
            backend=self.backend,
            max_length=self.max_length,
        )
        self.searches.append((len(statements), len(code), found))
        if found.instructions is None:
            return None
        return self.statements(found, spec, routine.start)

    def statements(self, found, spec, origin: int) -> tuple:  # pylint: disable=R0914
        """The grammar statements of a found sequence."""
        symbols = {}
        for v in spec.vars:
            symbols.update((v.address + k, Mem(v.name, k)) for k in range(v.size))
        out, pc, code = [], origin, found.code
        while pc - origin < len(code):
            mnemonic, mode = OPCODES[code[pc - origin]]
            size = MODE_LEN[mode]
            value = int.from_bytes(code[pc - origin + 1 : pc - origin + size], "little")
            operand = None
            if mode == "imm":
                operand = Imm(value)
                if pc in found.immediates:
                    name, high = found.immediates[pc]
                    byte = ">" if high else "<" if name[1:] in self.wide else ""
                    operand = Imm(name[1:], byte)
            elif mode in ("zp", "abs"):
                operand = symbols[value]
            elif mode in ("absx", "absy"):
                table = next(t for t in spec.tables if t.address <= value < t.address + t.length)
                offset = value - table.address
                half, offset = ("lo", offset) if offset < 256 else ("hi", offset - 256)
                operand = Mem(f"{table.name}.{half}", offset, mode[-1])
            out.append(op(mnemonic.lower(), operand))
            pc += size
        return tuple(out)
