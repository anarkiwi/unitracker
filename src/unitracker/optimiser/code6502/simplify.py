"""Equality saturation of output terms with egglog: bitvector algebra, the 6502's flag idioms
and byte carry chains raised to word operations; extraction by a cost approximating 6502
bytes. :func:`baseline` is z3's own ``simplify`` for comparison."""

import re

import z3
from egglog.bindings import EGraph

from unitracker.optimiser.code6502.terms import parse, sexp

SORTS = """
(datatype*
  (B (BC i64 :cost 2) (BV String :cost 2) (Lo W :cost 0) (Hi W :cost 0)
     (Add B B :cost 3) (Sub B B :cost 3) (And B B :cost 2) (Or B B :cost 2) (Xor B B :cost 2)
     (Shl B i64 :cost 1) (Shr B i64 :cost 1) (Ite P B B :cost 4) (Bit P :cost 5)
     (TabB String W :cost 2))
  (W (WC i64 :cost 4) (WV String :cost 4) (Cat B B :cost 1) (Zext B :cost 3) (Sext B :cost 8)
     (WAdd W W :cost 8) (WSub W W :cost 8) (WAnd W W :cost 6) (WOr W W :cost 6)
     (WXor W W :cost 6) (WShl W W :cost 9) (WShr W W :cost 9) (WIte P W W :cost 6)
     (Tab String W :cost 1))
  (P (PT :cost 0) (PF :cost 0) (Not P :cost 0) (PAnd P P :cost 2) (POr P P :cost 2)
     (Eq B B :cost 3) (Ult B B :cost 3) (Ule B B :cost 4) (WEq W W :cost 6) (WUlt W W :cost 6)
     (WUle W W :cost 7) (Sgn B :cost 1) (Odd B :cost 2) (AdcC B B P :cost 12)
     (SbcC B B P :cost 12) (WAdcC W W P :cost 20) (WSbcC W W P :cost 20)))
"""

FOLD = """
(rewrite (Add (BC a) (BC b)) (BC (& (+ a b) 255)))
(rewrite (Sub (BC a) (BC b)) (BC (& (- a b) 255)))
(rewrite (And (BC a) (BC b)) (BC (& a b)))
(rewrite (Or (BC a) (BC b)) (BC (| a b)))
(rewrite (Xor (BC a) (BC b)) (BC (^ a b)))
(rewrite (Shl (BC a) k) (BC (& (<< a k) 255)))
(rewrite (Shr (BC a) k) (BC (>> a k)))
(rewrite (Lo (WC k)) (BC (& k 255)))
(rewrite (Hi (WC k)) (BC (>> k 8)))
(rewrite (Cat (BC h) (BC l)) (WC (| (<< h 8) l)))
(rewrite (Zext (BC k)) (WC k))
(rewrite (WAdd (WC a) (WC b)) (WC (& (+ a b) 65535)))
(rewrite (WSub (WC a) (WC b)) (WC (& (- a b) 65535)))
(rewrite (WAnd (WC a) (WC b)) (WC (& a b)))
(rewrite (WShl (WShl x (WC a)) (WC b)) (WShl x (WC (+ a b))))
(rewrite (WShr (WShr x (WC a)) (WC b)) (WShr x (WC (+ a b))))
(rewrite (Eq (BC a) (BC b)) (PT) :when ((= a b)))
(rewrite (Eq (BC a) (BC b)) (PF) :when ((!= a b)))
(rewrite (Ult (BC a) (BC b)) (PT) :when ((< a b)))
(rewrite (Ult (BC a) (BC b)) (PF) :when ((>= a b)))
(rewrite (Sgn (BC a)) (PT) :when ((>= a 128)))
(rewrite (Sgn (BC a)) (PF) :when ((< a 128)))
(rewrite (WEq (WC a) (WC b)) (PT) :when ((= a b)))
(rewrite (WEq (WC a) (WC b)) (PF) :when ((!= a b)))
"""

ALGEBRA = """
(rewrite (Add a (BC 0)) a)
(rewrite (Sub a (BC 0)) a)
(rewrite (Sub a a) (BC 0))
(rewrite (And a (BC 255)) a)
(rewrite (And a (BC 0)) (BC 0))
(rewrite (And a a) a)
(rewrite (Or a (BC 0)) a)
(rewrite (Or a a) a)
(rewrite (Xor a (BC 0)) a)
(rewrite (Xor a a) (BC 0))
(rewrite (Add a b) (Add b a))
(rewrite (And a b) (And b a))
(rewrite (Or a b) (Or b a))
(rewrite (Xor a b) (Xor b a))
(rewrite (Add (Add a b) c) (Add a (Add b c)))
(rewrite (Sub (Add a b) b) a)
(rewrite (Xor a (BC 255)) (Sub (BC 255) a))
(rewrite (Add (Sub (BC a) x) (BC b)) (Sub (BC (& (+ a b) 255)) x))
(rewrite (Sub (Sub (BC a) x) (BC b)) (Sub (BC (& (- a b) 255)) x))
(rewrite (Sub a (Add b c)) (Sub (Sub a b) c))
(rewrite (And (And a b) c) (And a (And b c)))
(rewrite (WAdd a (WC 0)) a)
(rewrite (WSub a (WC 0)) a)
(rewrite (WSub a a) (WC 0))
(rewrite (WShl a (WC 0)) a)
(rewrite (WShr a (WC 0)) a)
(rewrite (WAdd a b) (WAdd b a))
(rewrite (WAdd (WAdd a b) c) (WAdd a (WAdd b c)))
(rewrite (WSub (WSub a b) c) (WSub a (WAdd b c)))
(rewrite (WSub (WAdd a b) b) a)
(rewrite (WAdd (WSub a b) b) a)
(rewrite (TabB t (Zext b)) (Lo (Tab t (Zext b))))
(rewrite (TabB t (WAdd (WC 256) (Zext b))) (Hi (Tab t (Zext b))))
(rewrite (TabB t (WAdd (Zext b) (WC 256))) (Hi (Tab t (Zext b))))
(rewrite (TabB t (WC k)) (Lo (Tab t (WC k))) :when ((< k 256)))
(rewrite (TabB t (WC k)) (Hi (Tab t (WC (- k 256)))) :when ((>= k 256) (< k 512)))
(rewrite (Lo (Cat h l)) l)
(rewrite (Hi (Cat h l)) h)
(rewrite (Cat (Hi w) (Lo w)) w)
(rewrite (Lo (Zext b)) b)
(rewrite (Hi (Zext b)) (BC 0))
(birewrite (Cat (BC 0) b) (Zext b))
(rewrite (Lo (Sext b)) b)
(rewrite (Hi (Sext b)) (Ite (Sgn b) (BC 255) (BC 0)))
(rewrite (Cat (Ite (Sgn b) (BC 255) (BC 0)) b) (Sext b))
(rewrite (WIte (Sgn b) (Cat (BC 255) b) (Cat (BC 0) b)) (Sext b))
(rewrite (Lo (WIte c a b)) (Ite c (Lo a) (Lo b)))
(rewrite (Hi (WIte c a b)) (Ite c (Hi a) (Hi b)))
(rewrite (Cat (Ite c h1 h2) (Ite c l1 l2)) (WIte c (Cat h1 l1) (Cat h2 l2)))
(rewrite (Cat (Ite c h1 h2) l) (WIte c (Cat h1 l) (Cat h2 l)))
(rewrite (Lo (WAnd a (WC 255))) (Lo a))
(rewrite (Hi (WAnd a (WC 255))) (BC 0))
(rewrite (And (Shl a k) (BC 255)) (Shl a k))
"""

LOGIC = """
(rewrite (Not (Not p)) p)
(rewrite (Not (PT)) (PF))
(rewrite (Not (PF)) (PT))
(rewrite (PAnd (PT) p) p)
(rewrite (PAnd (PF) p) (PF))
(rewrite (POr (PF) p) p)
(rewrite (POr (PT) p) (PT))
(rewrite (PAnd p p) p)
(rewrite (POr p p) p)
(rewrite (PAnd a b) (PAnd b a))
(rewrite (POr a b) (POr b a))
(rewrite (Ule a b) (Not (Ult b a)))
(rewrite (WUle a b) (Not (WUlt b a)))
(rewrite (Eq a a) (PT))
(rewrite (Eq a b) (Eq b a))
(rewrite (Eq (Sub a b) (BC 0)) (Eq a b))
(rewrite (WEq (WSub a b) (WC 0)) (WEq a b))
(rewrite (Eq (And a (BC 128)) (BC 0)) (Not (Sgn a)))
(rewrite (Ult (BC 127) a) (Sgn a))
(rewrite (Eq (And a (BC 1)) (BC 1)) (Odd a))
(rewrite (Eq (And a (BC 1)) (BC 0)) (Not (Odd a)))
(rewrite (And a (BC 1)) (Bit (Odd a)))
(rewrite (Eq (Or a b) (BC 0)) (PAnd (Eq a (BC 0)) (Eq b (BC 0))))
(rewrite (PAnd (Eq (Lo w) (BC 0)) (Eq (Hi w) (BC 0))) (WEq w (WC 0)))
(rewrite (Eq (Bit p) (BC 1)) p)
(rewrite (Eq (Bit p) (BC 0)) (Not p))
(rewrite (Ite c (BC 1) (BC 0)) (Bit c))
(rewrite (Bit (PT)) (BC 1))
(rewrite (Bit (PF)) (BC 0))
(rewrite (Or (Bit p) (Bit q)) (Bit (POr p q)))
(rewrite (And (Bit p) (Bit q)) (Bit (PAnd p q)))
(rewrite (Sub (BC 1) (Bit p)) (Bit (Not p)))
(rewrite (Xor (Bit p) (BC 1)) (Bit (Not p)))
(rewrite (Shr (Bit p) 1) (BC 0))
(rewrite (Odd (Bit p)) p)
(rewrite (Ite (PT) a b) a)
(rewrite (Ite (PF) a b) b)
(rewrite (Ite c a a) a)
(rewrite (Ite (Not c) a b) (Ite c b a))
(rewrite (WIte (PT) a b) a)
(rewrite (WIte (PF) a b) b)
(rewrite (WIte c a a) a)
(rewrite (WIte (Not c) a b) (WIte c b a))
(rewrite (Odd (Add a (BC k))) (Odd a) :when ((= (& k 1) 0)))
(rewrite (Odd (Xor a (BC k))) (Not (Odd a)) :when ((= (& k 1) 1)))
(rewrite (WIte c (WAdd a x) (WAdd a y)) (WAdd a (WIte c x y)))
(rewrite (WIte c (WSub a x) (WSub a y)) (WSub a (WIte c x y)))
"""

CARRY = """
(rule ((= x (BC 0))) ((union x (Bit (PF)))))
(rule ((= x (BC 1))) ((union x (Bit (PT)))))
(rewrite (POr (Ult (Add a b) a) (Ult (Add (Add a b) (Bit c)) (Add a b))) (AdcC a b c))
(birewrite (AdcC a b (PF)) (Ult (Add a b) a))
(rewrite (WUle (WAdd (Zext b) (Zext (Sub (BC 1) (Bit c)))) (Zext a)) (SbcC a b c))
(birewrite (SbcC a b (PT)) (Not (Ult a b)))
(rewrite (Ule b a) (SbcC a b (PT)))
(rewrite (AdcC ah bh (AdcC al bl c)) (WAdcC (Cat ah al) (Cat bh bl) c))
(rewrite (SbcC ah bh (SbcC al bl c)) (WSbcC (Cat ah al) (Cat bh bl) c))
(rewrite (WSbcC a b (PT)) (Not (WUlt a b)))
(rewrite (WAdcC a b (PF)) (WUlt (WAdd a b) a))
(rewrite (Cat (Add (Add ah bh) (Bit (AdcC al bl c))) (Add (Add al bl) (Bit c)))
         (WAdd (WAdd (Cat ah al) (Cat bh bl)) (Zext (Bit c))))
(rewrite (Cat (Sub ah (Add bh (Sub (BC 1) (Bit (SbcC al bl c)))))
              (Sub al (Add bl (Sub (BC 1) (Bit c)))))
         (WSub (WSub (Cat ah al) (Cat bh bl)) (Zext (Sub (BC 1) (Bit c)))))
(rewrite (Sub (Sub ah bh) (Bit (Ult al bl))) (Hi (WSub (Cat ah al) (Cat bh bl))))
(rewrite (Add (Add ah bh) (Bit (AdcC al bl (PF)))) (Hi (WAdd (Cat ah al) (Cat bh bl))))
(rewrite (Eq (Ite p (BC 1) (BC 0)) x) (Eq (Sub x (Bit p)) (BC 0)))
(rewrite (Eq x (Ite p (BC 1) (BC 0))) (Eq (Sub x (Bit p)) (BC 0)))
(rewrite (Cat (Or (Shl h 1) (Bit (Sgn l))) (Shl l 1)) (WShl (Cat h l) (WC 1)))
(rewrite (Cat (Shr h 1) (Or (Shr l 1) (Shl (And h (BC 1)) 7))) (WShr (Cat h l) (WC 1)))
"""

CONTROL = """
(rewrite (POr (Eq a b) (Ult a b)) (Not (Ult b a)))
(rewrite (PAnd (Not (Eq a b)) (Not (Ult a b))) (Ult b a))
(rewrite (Ite p x (Ite q x y)) (Ite (POr p q) x y))
(rewrite (Ite p (Ite q x y) y) (Ite (PAnd p q) x y))
(rewrite (WIte p x (WIte q x y)) (WIte (POr p q) x y))
(rewrite (WIte p (WIte q x y) y) (WIte (PAnd p q) x y))
"""

ITERATIONS = 12
NODES = 200_000


def _ruleset(name: str, text: str) -> str:
    """``text``'s rules, each in ruleset ``name``."""
    out, depth, start = [f"(ruleset {name})"], 0, None
    for k, ch in enumerate(text):
        if ch == "(":
            start = k if depth == 0 else start
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                out.append(f"{text[start:k]} :ruleset {name})")
    return "\n".join(out)


RULES = _ruleset("core", FOLD + ALGEBRA + LOGIC + CARRY) + "\n" + _ruleset("control", CONTROL)


def saturate(outputs: dict, iterations: int = ITERATIONS, nodes: int = NODES) -> dict:
    """``{name: term}`` of the cheapest equivalent of each term of ``outputs``: ``iterations``
    rounds of the core rules, two of the control rules, the core rules again, each phase under
    egglog's back-off scheduler and skipped once the e-graph holds ``nodes`` e-nodes."""
    names, lets = {}, []
    roots = {name: sexp(term, names, lets) for name, term in outputs.items()}
    graph = EGraph()
    program = [SORTS, RULES]
    program += [f"(let {n} {text})" for n, text in lets]
    program += [f"(let $o{k} {root})" for k, root in enumerate(roots.values())]
    graph.parse_and_run_program("\n".join(program))
    for ruleset, rounds in (("core", iterations), ("control", 2), ("core", iterations)):
        if _size(graph) > nodes:
            break
        schedule = f"(repeat {rounds} (run-with bo {ruleset}))"
        graph.parse_and_run_program(f"(run-schedule (let-scheduler bo (back-off)) {schedule})")
    results = graph.parse_and_run_program("\n".join(f"(extract $o{k})" for k in range(len(roots))))
    return {name: parse(str(r)) for name, r in zip(roots, results)}


def _size(graph) -> int:
    """The e-nodes in ``graph``."""
    out = graph.parse_and_run_program("(print-size)")[-1]
    return sum(int(n) for n in re.findall(r"(\d+)\)", str(out)))


def baseline(outputs: dict) -> dict:
    """z3's ``simplify`` of each z3 term of ``outputs``."""
    return {name: z3.simplify(term) for name, term in outputs.items()}
