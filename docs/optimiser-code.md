# Code modulator optimiser (prototype)

`unitracker.optimiser.code6502` rewrites a format 1.23 code template into one canonical 6502
routine with the same outputs, proved equal by z3 and by the player. It answers whether the
generic optimiser can canonicalise lifted routines automatically, and at what cost in size.

```sh
python -m unitracker.optimiser.code6502 unitracker.direct.gt2code [MORE.py ...] \
    [--only vib,note] [--cases 2000] [--superopt 5] [-j N] [--show] [-o table.md]
```

A module is anything defining `CODE` (`{name: template text}`) and `STATE` (`{cell: {var:
size}}`); each template's cell is the one whose variables it names. The output is the table
below for the modules given; `--show` prints each optimised template.

## Pipeline

| step | module | how |
|---|---|---|
| semantics | `semantics` | the template assembled by unitracker-player's `Machine`, its interface a jennings superopt `Spec` (`v`, `phi`, `nn`, `ne`, state variables; each table a 512-byte region `T.lo`,`T.hi`; parameters `#p` 16 bits bound to their immediates; A, X, Y, flags 0 but I); `jennings.superopt.execute` runs it symbolically over deity-informant's p-code: one z3 term per output, branches merged as if-then-else, path-forced values constant, counted shift loops (`ldx #k; ... lsr; ror; dex; bne`) a variable shift by the parameter |
| validation | `concrete`, `pipeline.check` | the z3 terms evaluated on random and edge-biased inputs equal `unitracker_player.code.Routine` on the same inputs; reads past `T.hi+255` are outside the model and skipped |
| simplification | `terms`, `simplify` | the terms as byte, word and predicate constructors in egglog: constant folding, bitvector algebra, the 6502's flag idioms (ADC/SBC/CMP carries, N as sign, `lsr` carry as parity) and byte carry chains raised to word add, subtract, compare and shift; extracted by a cost approximating bytes; `z3.simplify` is the baseline |
| canonical control | `diagram` | the atoms of every if-then-else are tested in the order (and with the output sequencing) whose code is smallest, priced by the selector itself; arms z3 proves infeasible are pruned, tests z3 proves redundant dropped, identical subtrees shared; an output may be written first (`seq`) so later tests read its new value, and a word shared by several leaves may be computed once into `phi` |
| codegen | `codegen` | a deterministic selector from the diagram to grammar statements: tests as compare and branch, leaves as straight-line code over the bytes and registers it knows hold each term, the carry tracked so `clc`/`sec` appear only where needed, outputs ordered for the smallest code, branches relaxed past their range |
| superoptimisation | `superopt` | optional: each final straight-line leaf searched by `jennings.superopt.search` (GPU through CuPy where present) for a shorter proved equivalent |
| proof | `pipeline` | z3 equivalence of the original and generated semantics on every output for all inputs (tables as arrays); a differential run of both on the player's `Routine` |
| canonicality | `groups` | routines of one shape (state sizes, parameter and table counts) z3 proves equal under some renaming of their state, parameters and tables form a group; the group is canonical where their optimised texts agree once renamed |

The semantics core and the superoptimiser are player-agnostic and live in jennings
(`jennings.superopt`, until released the `superopt` branch of anarkiwi/jennings).

## Results

RESULTS

## Limits

- Control is a decision diagram over atoms: code whose original shares a tail after a join
  mid-computation (the table walkers `ptab`, `ptabs`, `tporta`) duplicates per path.
- Only straight-line final leaves are superoptimised; exhaustive search reaches about five
  instructions at the alphabet sizes of these templates.
- Shift loops are recognised semantically only in the counted `dex`/`dey` + `bne` form; other
  loops need their counter bound (`semantics(..., bind={"k": 3})`).
