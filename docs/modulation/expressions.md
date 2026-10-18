# Modulators

A modulator drives one cell of the player (a voice's frequency, its pulse width, or the filter
cutoff) by an integer expression evaluated at each of the unit's firings.

## Grammar

| form | meaning |
|---|---|
| `12` | an integer |
| `a + b`, `a - b`, `a * b`, `-a` | integer arithmetic, unbounded |
| `a // b` | floored division: rounds toward minus infinity |
| `a % b` | floored modulo: the result has the sign of `b` |
| `abs(a)`, `min(a, b)`, `max(a, b)` | |
| `[a op b]` | Iverson bracket of one comparison: 1 when it holds, else 0; `op` is `==`, `!=`, `<`, `<=`, `>` or `>=` |
| `v` | the cell's value before the firing |
| `φ` | the firings since the modulator started, from 0 |
| `N` | the voice's sounding note, format numbering (C2 = 24) |
| `F(k)` | the player's frequency word of format note `k` |
| `T[k]` | entry `k` of a table `T` the playroutine declares (`modulators: tables:`); 0 outside its size |

Anything else is rejected on reading, naming the text (`... is outside the grammar`,
`... calls no grammar function`, `[...] is not one comparison`, `unknown table 'T'`).

## Evaluation

Each firing of an active expression `f` computes, from the state before it,

```
v' = f(v, φ, N)        stored in the cell, wrapped to its width
φ' = φ + 1
```

A start opens an occurrence at the unit's next firing, with `φ = 0` and `v` the unit's value
then; it runs until the unit's next start. An expression with no `v` is a closed form in `φ`:
the value after firing `φ`. A plain integer in an expression slot is a constant expression.
Values a modulator carries from earlier playback (a slide's start frequency, a delay counter, an
accumulator's direction) are constants of its expression, which the lifter resolves at the
modulator's start. A start that selects no expression leaves the unit to the playroutine's own
firing.

## Units

A unit drives one shared key, `freq` (a voice's frequency), `pw` (a voice's pulse width) or `cut`
(the filter cutoff). Its value `v` is its `chip` map's value, else its cell's (see [Modulator
declarations](../playroutines/schema.md#modulator-declarations)); each playroutine's file gives its
units ([goattracker2](../playroutines/goattracker2.md#modulators),
[sidwizard](../playroutines/sidwizard.md#modulators),
[defmon](../playroutines/defmon.md#modulators)).

## Where expressions go

| place | written as |
|---|---|
| a program row's `pw`, `cut` or `freq` | the row key's expression |
| an instrument's `pw` or `filter` program | the program's one expression |
| an instrument's `freq` | the pitch expression a note starting the instrument runs |
| an event's `!freq:@x1` | a name of an `x` definition |

A key that also takes a plain value reads an integer as that value and a string as an
expression (`cut: '1024'` is the constant expression, written back quoted); elsewhere an integer
is a constant expression. An expression that contains a comma is quoted inside a YAML flow map:
`pw: '2*min(64, φ) - 8*min(64, max(0, φ - 65)) + 2048'`.

## Legato

`!legato:0` makes the event's note sound without retriggering it: no gate-off, hard restart or
instrument start. The playroutine's `commands` entry named `legato` gives its code, and
`modulators.legato` the cell it sets.
