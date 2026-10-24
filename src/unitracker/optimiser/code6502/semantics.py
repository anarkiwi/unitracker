"""A code template as a jennings superopt spec and its z3 semantics, one term per output (``v``,
each state variable): ``v``, ``phi``, ``nn``, ``ne`` and the state are variables, each table a
512-byte region (``T.lo`` then ``T.hi``), each parameter ``#p`` 16 bits; flags clear but I."""

from dataclasses import dataclass, field

from jennings.opcodes import MODE_LEN, OPCODES
from jennings.superopt.symbolic import Spec, SymbolicError, Table, Var, execute
from unitracker_grammar.code import Imm, Op, params, symbols
from unitracker_player.code import Machine

SYMBOLS = (("v", 2), ("phi", 2), ("nn", 1), ("ne", 1))
CELL = "c"
SPAN = 256
ENTRY = {"A": 0, "X": 0, "Y": 0, "C": 0, "Z": 0, "N": 0, "V": 0, "D": 0, "I": 1}
SemanticsError = SymbolicError


@dataclass(frozen=True)
class Shape:
    """What a routine reads and writes: its state ``((name, size), ...)``, its parameters and
    its tables (``F`` among them where it reads the note table)."""

    state: tuple
    params: tuple
    tables: tuple

    @classmethod
    def of(cls, statements, state: dict) -> "Shape":
        """The shape of ``statements`` in a cell declaring ``state`` ``{name: size}``."""
        named = symbols(statements)
        tables = tuple(sorted({s.split(".")[0] for s in named if "." in s}))
        return cls(tuple(state.items()), params(statements), tables)

    def outputs(self) -> tuple:
        """The names of the outputs, ``v`` then the state variables."""
        return ("v",) + tuple(n for n, _ in self.state)

    def inputs(self) -> dict:
        """``{name: z3 term}`` of every input (parameters as ``#p``)."""
        return layout(self)[1].inputs()


@dataclass
class Semantics:
    """A routine's outputs as z3 terms over its :class:`Shape`'s inputs, and the loops
    summarised as shifts ``(kind, variable, bits)``."""

    shape: Shape
    outputs: dict
    loops: list = field(default_factory=list)


def layout(shape: Shape):
    """``(machine, spec)`` of a shape: a machine with its variables and tables laid out and
    the jennings spec of that interface."""
    tables = {t: [0] * SPAN for t in shape.tables if t != "F"}
    machine = Machine(tables, {CELL: shape.state})
    if "F" in shape.tables:
        machine.symbols["F.lo"], machine.symbols["F.hi"] = machine.top, machine.top + SPAN
        machine.top += 2 * SPAN
    variables = tuple(
        Var(name, machine.address(name, CELL, "r"), size) for name, size in SYMBOLS + shape.state
    )
    regions = tuple(Table(t, machine.symbols[f"{t}.lo"], 2 * SPAN) for t in shape.tables)
    spec = Spec(
        vars=variables,
        tables=regions,
        params=tuple(f"#{p}" for p in shape.params),
        entry=dict(ENTRY),
        outputs=shape.outputs(),
    )
    return machine, spec


@dataclass
class Assembly:
    """A template assembled on its own machine: the memory, its code's span, the jennings
    spec of its interface and its parameter immediates ``{pc: (#p, high)}``."""

    memory: bytearray
    start: int
    end: int
    spec: Spec
    immediates: dict
    machine: Machine

    @classmethod
    def of(cls, statements, shape: Shape, bind=None) -> "Assembly":
        """``statements`` of ``shape`` assembled, the parameters in ``bind`` concrete."""
        bind = dict(bind or {})
        machine, spec = layout(shape)
        bag = {p: bind.get(p, 0) for p in shape.params}
        routine = machine.compile("r", statements, bag, CELL)
        immediates, pc = {}, routine.start
        for s in (s for s in statements if isinstance(s, Op)):
            imm = s.operand
            if isinstance(imm, Imm) and isinstance(imm.value, str) and imm.value not in bind:
                immediates[pc] = (f"#{imm.value}", imm.byte == ">")
            pc += MODE_LEN[OPCODES[machine.memory[pc]][1]]
        return cls(machine.memory, routine.start, routine.end, spec, immediates, machine)


def semantics(statements, state: dict, bind=None) -> Semantics:
    """The :class:`Semantics` of checked ``statements`` in a cell declaring ``state``
    ``{name: size}``, the parameters in ``bind`` ``{name: int}`` concrete."""
    shape = Shape.of(statements, state)
    code = Assembly.of(statements, shape, bind)
    sem = execute(code.memory, code.start, code.end, code.spec, code.immediates)
    return Semantics(shape, sem.outputs, sem.loops)
