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

Each template of `gt2code`, `dmcode`, `macode` and SID-Wizard's `swcode` build
(`templates(True, True) | track_templates(False) | mirror_templates()`): instructions, bytes
and worst-case cycles (a loop at 255 trips) before → after; output-term DAG sizes as lifted, after
`z3.simplify` and after egglog; z3 proof; player differential (agreeing / inputs inside the
model, 2000 drawn).

| routine | instructions | bytes | worst cycles | terms lifted / z3 / egglog | leaves superoptimised | proved | differential |
|---|---|---|---|---|---|---|---|
| gt2code.note | 13 → 14 | 26 → 27 | 41 → 41 | 19 / 14 / 16 | 0/0 | yes | 2000/2000 |
| gt2code.snap | 10 → 10 | 20 → 20 | 33 → 33 | 7 / 6 / 6 | 0/0 | yes | 2000/2000 |
| gt2code.porta | 19 → 18 | 34 → 33 | 32 → 31 | 34 / 19 / 9 | 0/0 | yes | 2000/2000 |
| gt2code.portan | 39 → 39 | 73 → 73 | 3901 → 3901 | 90 / 55 / 28 | 0/0 | yes | 2000/2000 |
| gt2code.tporta | 45 → 93 | 92 → 190 | 96 → 115 | 90 / 75 / 37 | 0/0 | yes | 2000/2000 |
| gt2code.vib | 31 → 36 | 57 → 67 | 60 → 67 | 71 / 51 / 35 | 0/0 | yes | 2000/2000 |
| gt2code.vibn | 51 → 56 | 96 → 106 | 3929 → 3936 | 138 / 89 / 55 | 0/0 | yes | 2000/2000 |
| gt2code.porta1 | 22 → 21 | 39 → 38 | 38 → 36 | 41 / 24 / 12 | 0/0 | yes | 2000/2000 |
| gt2code.portan1 | 42 → 41 | 78 → 77 | 3907 → 3904 | 97 / 60 / 32 | 0/0 | yes | 2000/2000 |
| gt2code.snap1 | 14 → 14 | 27 → 27 | 42 → 41 | 19 / 13 / 15 | 0/0 | yes | 2000/2000 |
| dmcode.ninit | 16 → 17 | 31 → 32 | 44 → 44 | 23 / 9 / 16 | 0/0 | yes | 2000/2000 |
| dmcode.nr | 10 → 13 | 19 → 22 | 30 → 28 | 22 / 8 / 13 | 0/0 | yes | 2000/2000 |
| dmcode.pitch | 31 → 43 | 59 → 85 | 66 → 60 | 50 / 40 / 35 | 0/0 | yes | 2000/2000 |
| dmcode.slide | 54 → 127 | 106 → 248 | 108 → 114 | 144 / 89 / 36 | 0/0 | yes | 2000/2000 |
| dmcode.por | 43 → 102 | 83 → 201 | 103 → 121 | 91 / 63 / 34 | 0/0 | yes | 1992/1992 |
| dmcode.pinit | 18 → 23 | 35 → 44 | 51 → 49 | 22 / 8 / 13 | 0/0 | yes | 2000/2000 |
| dmcode.pzero | 7 → 8 | 13 → 14 | 23 → 23 | 16 / 6 / 9 | 0/0 | yes | 2000/2000 |
| dmcode.pmode | 29 → 63 | 56 → 116 | 60 → 58 | 103 / 55 / 38 | 0/0 | yes | 2000/2000 |
| dmcode.acid | 30 → 38 | 55 → 69 | 74 → 71 | 61 / 26 / 37 | 0/0 | yes | 2000/2000 |
| swall.tone | 27 → 49 | 51 → 95 | 77 → 78 | 51 / 25 / 22 | 0/0 | yes | 2000/2000 |
| swall.det | 42 → 434 | 81 → 790 | 86 → 136 | 255 / 146 / 86 | 0/0 | yes | 2000/2000 |
| swall.dfx | 36 → 258 | 69 → 469 | 72 → 117 | 172 / 104 / 50 | 0/0 | yes | 2000/2000 |
| swall.snd | 57 → 361 | 113 → 703 | 116 → 145 | 146 / 69 / 78 | 0/0 | yes | 2000/2000 |
| swall.hrc | 6 → 7 | 11 → 12 | 19 → 19 | 12 / 6 / 8 | 0/0 | yes | 2000/2000 |
| swall.wt | 91 → 313 | 167 → 594 | 109 → 153 | 276 / 143 / 92 | 0/0 | yes | 2000/2000 |
| swall.vfq | 92 → 685 | 170 → 1280 | 113 → 157 | 366 / 165 / 118 | 0/0 | yes | 2000/2000 |
| swall.kty | 92 → 765 | 170 → 1423 | 113 → 153 | 357 / 184 / 132 | 0/0 | yes | 2000/2000 |
| macode.pulse | 15 → 14 | 27 → 26 | 33 → 31 | 22 / 15 / 12 | 0/0 | yes | 2000/2000 |
| macode.pong | 37 → 38 | 71 → 71 | 66 → 65 | 54 / 35 / 29 | 0/0 | yes | 2000/2000 |
| macode.sweep | 26 → 31 | 46 → 55 | 67 → 83 | 49 / 39 / 26 | 0/0 | yes | 2000/2000 |

| equivalence group | identical optimised text |
|---|---|
| (none) | |

| failed | why |
|---|---|
| gt2code.ptab | DiagramError: no diagram within 60000 steps |
| gt2code.ptabs | DiagramError: no diagram within 60000 steps |
| gt2code.tportan | DiagramError: no diagram |
| gt2code.tporta1 | CodegenError: no free word for an intermediate |
| gt2code.tportan1 | DiagramError: no diagram |
| dmcode.pul | DiagramError: no diagram within 60000 steps |
| dmcode.cinit | DiagramError: no diagram within 60000 steps |
| swall.stp | CodegenError: no order of the outputs computes them |
| swall.vfx | DiagramError: no diagram within 60000 steps |
| swall.sld | DiagramError: no diagram within 60000 steps |
| swall.por | CodegenError: no order of the outputs computes them |
| swall.leg | CodegenError: no order of the outputs computes them |
| swall.npn | CodegenError: no order of the outputs computes them |
| swall.pst | DiagramError: no diagram within 60000 steps |
| swall.psw | DiagramError: no diagram |
| swall.pid | DiagramError: no diagram within 60000 steps |
| swall.pwr | DiagramError: no diagram |
| swall.fst | DiagramError: no diagram within 60000 steps |
| swall.fsw | DiagramError: no diagram within 60000 steps |
| swall.fho | DiagramError: no diagram within 60000 steps |
| swall.fid | DiagramError: no diagram within 60000 steps |
| swall.fhi | DiagramError: no diagram within 60000 steps |
| swall.frs | DiagramError: no diagram |
| swall.mst | DiagramError: no diagram within 60000 steps |
| swall.msw | DiagramError: no diagram |
| swall.mid | DiagramError: no diagram within 60000 steps |
| swall.mwr | DiagramError: no diagram |
| macode.vib | DiagramError: no diagram within 60000 steps |

Variant pairs written two ways (`tests/test_optimiser_code6502.py`: a note set by `ora`/`bne`
and `x` against two `bne`s and `y`; a signed add by `bmi` and `sec`/`sbc` against `asl`/`bcs`
and `stx` reordered) are proved equal and print identically.

With `--superopt 5` (final straight-line leaves searched up to five instructions by
`jennings.superopt` on an RTX 5080, four routines at a time), for the templates whose
diagrams are found:

| routine | instructions | bytes | worst cycles | terms lifted / z3 / egglog | leaves superoptimised | proved | differential |
|---|---|---|---|---|---|---|---|
| gt2code.note | 13 → 14 | 26 → 27 | 41 → 41 | 19 / 14 / 16 | 0/1 | yes | 2000/2000 |
| gt2code.snap | 10 → 10 | 20 → 20 | 33 → 33 | 7 / 6 / 6 | 0/1 | yes | 2000/2000 |
| gt2code.porta | 19 → 18 | 34 → 33 | 32 → 31 | 34 / 19 / 9 | 0/2 | yes | 2000/2000 |
| gt2code.portan | 39 → 39 | 73 → 73 | 3901 → 3901 | 90 / 55 / 28 | 0/2 | yes | 2000/2000 |
| gt2code.vib | 31 → 36 | 57 → 67 | 60 → 67 | 71 / 51 / 35 | 0/2 | yes | 2000/2000 |
| gt2code.vibn | 51 → 56 | 96 → 106 | 3929 → 3936 | 138 / 89 / 55 | 0/2 | yes | 2000/2000 |
| gt2code.porta1 | 22 → 21 | 39 → 38 | 38 → 36 | 41 / 24 / 12 | 0/2 | yes | 2000/2000 |
| gt2code.portan1 | 42 → 41 | 78 → 77 | 3907 → 3904 | 97 / 60 / 32 | 0/2 | yes | 2000/2000 |
| gt2code.snap1 | 14 → 14 | 27 → 27 | 42 → 41 | 19 / 13 / 15 | 0/1 | yes | 2000/2000 |
| dmcode.ninit | 16 → 17 | 31 → 32 | 44 → 44 | 23 / 9 / 16 | 0/1 | yes | 2000/2000 |
| dmcode.nr | 10 → 12 | 19 → 21 | 30 → 28 | 22 / 8 / 13 | 1/2 | yes | 2000/2000 |
| dmcode.pitch | 31 → 43 | 59 → 85 | 66 → 60 | 50 / 40 / 35 | 0/3 | yes | 2000/2000 |
| dmcode.pinit | 18 → 23 | 35 → 44 | 51 → 49 | 22 / 8 / 13 | 0/2 | yes | 2000/2000 |
| dmcode.pzero | 7 → 8 | 13 → 14 | 23 → 23 | 16 / 6 / 9 | 0/1 | yes | 2000/2000 |
| dmcode.acid | 30 → 37 | 55 → 68 | 74 → 71 | 61 / 26 / 37 | 1/2 | yes | 2000/2000 |
| swall.tone | 27 → 49 | 51 → 95 | 77 → 78 | 51 / 25 / 22 | 0/3 | yes | 2000/2000 |
| swall.hrc | 6 → 7 | 11 → 12 | 19 → 19 | 12 / 6 / 8 | 0/1 | yes | 2000/2000 |
| macode.pulse | 15 → 14 | 27 → 26 | 33 → 31 | 22 / 15 / 12 | 0/2 | yes | 2000/2000 |
| macode.pong | 37 → 37 | 71 → 70 | 66 → 63 | 54 / 35 / 29 | 1/2 | yes | 2000/2000 |
| macode.sweep | 26 → 31 | 46 → 55 | 67 → 83 | 49 / 39 / 26 | 0/2 | yes | 2000/2000 |

superopt: 36 leaves searched, 3 shorter, 1.75e+13 candidates in 1194 s (1.47e+10/s)

## Limits

- Control is a decision diagram over atoms: code whose original shares a tail after a join
  mid-computation (the table walkers `ptab`, `ptabs`, `tporta`, SID-Wizard's `det`, `kty`)
  duplicates per path.
- The diagram search is bounded in steps (diagram states and z3 checks, each check under z3's
  deterministic resource limit): greedy past 20000, no diagram past 60000.
- Flags used as data (`php`/`plp`, SID-Wizard's `_TRACK`) give terms the selector has no
  pattern for (`CodegenError`, `DiagramError: no diagram`).
- Only straight-line final leaves are superoptimised; exhaustive search reaches about five
  instructions at the alphabet sizes of these templates.
- Shift loops are recognised semantically only in the counted `dex`/`dey` + `bne` form; other
  loops need their counter bound (`semantics(..., bind={"k": 3})`).
