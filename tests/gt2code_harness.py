"""Format 1.23 code modulators on an emulated 6502: canonical syntax, assembly, one firing.

:class:`Routine` assembles a template with its parameters and lays out ``v``, ``phi``, ``nn``,
``ne``, the cell's state and the tables; :meth:`Routine.fire` runs one firing (spec section 3).
"""

import re

from jennings.assembler import Assembler
from jennings.devices.mpu6502 import MPU

MNEMONICS = frozenset(
    "adc and asl bcc bcs beq bit bmi bne bpl bvc bvs clc cld cli clv cmp cpx cpy dec dex dey "
    "eor inc inx iny jmp lda ldx ldy lsr nop ora pha php pla plp rol ror rts sbc sec sed sei "
    "sta stx sty tax tay tsx txa txs tya".split()
)
BRANCHES = frozenset("bcc bcs beq bmi bne bpl bvc bvs jmp".split())
RESERVED = frozenset(("v", "phi", "nn", "ne", "a", "x", "y"))
NAME = r"[a-z][a-z0-9_]*"
LABEL = re.compile(rf"^({NAME}):$")
INSTRUCTION = re.compile(r"^([a-z]{3})(?: (\S+))?$")
IMMEDIATE = re.compile(rf"^#(?:(0|[1-9]\d*)|([<>]?)({NAME}))$")
MEMORY = re.compile(rf"^({NAME}|[A-Z][A-Z0-9_]*\.(?:lo|hi))(?:\+([1-9]\d*))?(,[xy])?$")
PUSH, PULL = {0x48: 1, 0x08: 1}, {0x68: 1, 0x28: 1, 0x60: 2}
REFUSED, RTS, LIMIT, ORIGIN, DATA = frozenset((0x00, 0x20, 0x40)), 0x60, 4096, 0x1000, 0x0200


class RoutineError(ValueError):
    """A code template the 1.23 convention refuses (the player's PlayError)."""


def parse(code: str) -> list:
    """``code``'s statements as ``(label, None)`` or ``(mnemonic, operand)``, checked canonical."""
    statements, labels = [], set()
    for line in code.split("\n"):
        if match := LABEL.match(line):
            name = match.group(1)
            if name in labels or name in RESERVED:
                raise RoutineError(f"label {name!r}")
            labels.add(name)
            statements.append((name, None))
            continue
        match = INSTRUCTION.match(line)
        if not match or match.group(1) not in MNEMONICS:
            raise RoutineError(f"statement {line!r}")
        statements.append((match.group(1), match.group(2) or ""))
    for mnemonic, operand in statements:
        if operand is not None and mnemonic in BRANCHES and operand not in labels:
            raise RoutineError(f"{mnemonic} to {operand!r}")
    if ("rts", "") not in statements:
        raise RoutineError("no rts")
    return statements


def params(code: str) -> list:
    """The parameter names ``code`` reads as immediates, in first-use order."""
    names = []
    for _, operand in parse(code):
        match = IMMEDIATE.match(operand or "")
        if match and match.group(3) and match.group(3) not in names:
            names.append(match.group(3))
    return names


def _operand(text: str, values: dict, symbols: dict) -> str:
    """A non-branch operand ``text`` in the assembler's hexadecimal syntax."""
    if not text:
        return ""
    if match := IMMEDIATE.match(text):
        number, part, name = match.groups()
        if name is not None:
            if name not in values:
                raise RoutineError(f"parameter {name!r} unbound")
            number = (values[name] % 65536) >> (8 if part == ">" else 0) & 255
        if int(number) > 255:
            raise RoutineError(f"immediate {text!r}")
        return f"#${int(number):02x}"
    if match := MEMORY.match(text):
        name, offset, index = match.groups()
        if name not in symbols:
            raise RoutineError(f"symbol {name!r}")
        return f"${symbols[name] + int(offset or 0):04x}{(index or '').upper()}"
    raise RoutineError(f"operand {text!r}")


def assemble(code: str, values: dict, symbols: dict, origin: int = ORIGIN) -> bytes:
    """Machine code of ``code`` at ``origin``: ``values`` binds each parameter to an integer,
    ``symbols`` each memory symbol (``v``, ``G.lo``, a state variable) to its address."""
    statements, asm, labels = parse(code), Assembler(MPU()), {}
    for _ in range(2):
        image, found = [], {}
        for mnemonic, text in statements:
            pc = origin + len(image)
            if text is None:
                found[mnemonic] = pc
            elif mnemonic in BRANCHES:
                target = labels.get(text, pc + 2)
                if mnemonic != "jmp" and not -128 <= target - pc - 2 < 128:
                    raise RoutineError(f"{mnemonic} {text} out of range")
                image += asm.assemble(f"{mnemonic} ${target:04x}", pc)
            else:
                try:
                    image += asm.assemble(f"{mnemonic} {_operand(text, values, symbols)}", pc)
                except SyntaxError as err:
                    raise RoutineError(f"{mnemonic} {text}") from err
        labels = found
    return bytes(image)


class Routine:  # pylint: disable=too-few-public-methods
    """A code template bound to ``values`` over ``tables`` (name to entries) with the cell's
    declared ``state`` (name to size in bytes), ready to fire."""

    def __init__(self, code: str, values: dict, tables: dict, state: dict):
        self.sizes = {"v": 2, "phi": 2, "nn": 1, "ne": 1} | state
        self.symbols, address = {}, DATA
        for name, size in self.sizes.items():
            self.symbols[name], address = address, address + size
        self.mpu = MPU()
        memory = self.mpu.memory
        for name, entries in tables.items():
            for half, shift in (("lo", 0), ("hi", 8)):
                self.symbols[f"{name}.{half}"] = address
                memory[address : address + len(entries)] = [(e >> shift) & 255 for e in entries]
                address += len(entries)
        self.data = slice(DATA, address)
        self.image = memory[self.data]
        machine = assemble(code, values, self.symbols)
        memory[ORIGIN : ORIGIN + len(machine)] = machine

    def fire(self, v: int, phi: int, nn: int, ne: int, state: dict) -> tuple:
        """One firing: ``(v', state')`` from the incoming ``v``, ``phi``, the notes and the
        voice's ``state``; refuses a routine past 4096 instructions or off its stack."""
        memory, inputs = self.mpu.memory, {"v": v, "phi": phi, "nn": nn, "ne": ne} | state
        memory[self.data] = self.image
        for name, size in self.sizes.items():
            at = self.symbols[name]
            memory[at : at + size] = (inputs[name] % (1 << 8 * size)).to_bytes(size, "little")
        self._run()
        out = {
            name: int.from_bytes(bytes(memory[at : at + self.sizes[name]]), "little")
            for name, at in self.symbols.items()
            if name in self.sizes
        }
        return out.pop("v"), {k: out[k] for k in state}

    def _run(self):
        mpu, memory = self.mpu, self.mpu.memory
        mpu.pc, mpu.a, mpu.x, mpu.y, mpu.sp, mpu.p = ORIGIN, 0, 0, 0, 255, MPU.INTERRUPT
        for _ in range(LIMIT + 1):
            op, depth = memory[mpu.pc], 255 - mpu.sp
            if op == RTS and not depth:
                return
            if op in REFUSED:
                raise RoutineError(f"opcode {op}")
            if depth + PUSH.get(op, 0) > 255 or depth < PULL.get(op, 0):
                raise RoutineError("stack")
            mpu.step()
        raise RoutineError("instruction limit")
