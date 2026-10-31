"""Whether a tune's play routine only flushes the player's ghost block to the SID.

One call of the routine is run symbolically over a memory nothing is assumed of
(``pysidtracker.playspec.tv.Machine6502`` over z3): a call into the player is an unknown rewrite
of all memory, and the cells the code itself stores to are its operands, free as well. The routine
is a plain flush exactly when every store to a SID register writes, at the time of the store, the
ghost byte of that register, for all memory.
"""

import z3
from pysidtracker.playspec.smt import symbolic_memory
from pysidtracker.playspec.tv import STACK, TVError, Machine6502, Path, entry_registers

SID = 0xD400
REGISTERS = 25


class _Modified(Exception):
    """A store into a byte of the routine's own instructions."""

    def __init__(self, addr: int):
        super().__init__(addr)
        self.addr = addr


class _Flush(Machine6502):
    """The symbolic machine, calls into the player abstracted and stores recorded."""

    def __init__(self, *args, entries=()):
        super().__init__(*args)
        self.entries = frozenset(entries)
        self.stores = []
        self.fresh = 0

    def step(self, path: Path) -> list:
        if path.pc in self.entries:
            self.fresh += 1
            path.mem = symbolic_memory(f"M{self.fresh}")
            path.regs = entry_registers({}) | {3: path.regs[3]}
            return self.control(path, {"ctrl": ("rts",), "len": 1})
        return super().step(path)

    def store(self, path: Path, addr, value) -> None:
        addr = z3.simplify(addr)
        if z3.is_bv_value(addr) and addr.as_long() in self.ops:
            raise TVError(f"stores into an opcode at ${addr.as_long():04x}")
        if z3.is_bv_value(addr) and addr.as_long() in self.code:
            raise _Modified(addr.as_long())
        self.stores.append((path.cond, addr, value, path.mem))
        super().store(path, addr, value)


def _run(memory: bytes, play: int, entries, sink: int, operands: set) -> tuple:
    """``(machine, own)``: the registers a store of the routine at ``play`` writes other than the
    ghost byte, with ``operands`` free."""
    mem0, solver = symbolic_memory("M0"), z3.Solver()
    cpu = _Flush(memory, operands, solver, mem0, entries=entries)
    cpu.run(Path(z3.BoolVal(True), entry_registers({}), mem0, play))
    own = set()
    for cond, addr, value, mem in cpu.stores:
        if z3.is_bv_value(addr):
            if addr.as_long() in cpu.code:
                raise _Modified(addr.as_long())
            reg = addr.as_long() - SID
            if 0 <= reg < REGISTERS and _differs(solver, cond, value, mem, sink + reg):
                own.add(reg)
        elif _reaches(solver, cond, addr, range(SID, SID + REGISTERS)):
            raise TVError("a store through a computed address reaches the SID")
        elif _reaches(solver, cond, addr, sorted(cpu.code)):
            raise TVError("a store through a computed address reaches the routine's code")
    return cpu, tuple(sorted(own))


def _differs(solver, cond, value, mem, ghost: int) -> bool:
    """Whether some memory makes a store under ``cond`` write other than the ghost byte."""
    solver.push()
    solver.add(cond, value != z3.Select(mem, ghost))
    found = solver.check() == z3.sat
    solver.pop()
    return found


def _reaches(solver, cond, addr, targets) -> bool:
    """Whether some memory makes a store under ``cond`` at ``addr`` hit one of ``targets``."""
    stack = set(STACK)
    solver.push()
    solver.add(cond, z3.Or([addr == a for a in targets if a not in stack] or [z3.BoolVal(False)]))
    found = solver.check() == z3.sat
    solver.pop()
    return found


def own_writes(memory: bytes, wrap) -> tuple:
    """The SID registers the play routine of ``wrap`` (its ``play`` address, the player's
    ``entries`` and the ``sink`` of the ghost block) writes other than as the ghost byte of the
    register, for all memory; the cells the routine stores to are free operands."""
    operands = set()
    while True:
        try:
            return _run(bytes(memory), wrap.play, wrap.entries, wrap.sink, operands)[1]
        except _Modified as modified:
            if modified.addr in operands:
                raise TVError(f"stores into its own code at ${modified.addr:04x}") from modified
            operands.add(modified.addr)
