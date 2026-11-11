"""A small linear-modelling layer over HiGHS.

Why not Pyomo: the model is ~200 rows, and the explanation layer needs direct access to
row duals, tagged by what each row means (shared network limit vs. well-specific). Owning
the matrix keeps that mapping explicit and the solve deterministic.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

import highspy
import numpy as np

INF = math.inf
Number = int | float


@dataclass(frozen=True, slots=True)
class Expr:
    """Affine expression: sum(coef * x[index]) + const."""

    terms: Mapping[int, float] = field(default_factory=dict)
    const: float = 0.0

    @staticmethod
    def total(items: Iterable[Expr | Number]) -> Expr:
        terms: defaultdict[int, float] = defaultdict(float)
        const = 0.0
        for item in items:
            if isinstance(item, Expr):
                for idx, coef in item.terms.items():
                    terms[idx] += coef
                const += item.const
            else:
                const += item
        return Expr(dict(terms), const)

    def __add__(self, other: Expr | Number) -> Expr:
        return Expr.total((self, other))

    __radd__ = __add__

    def __neg__(self) -> Expr:
        return Expr({i: -c for i, c in self.terms.items()}, -self.const)

    def __sub__(self, other: Expr | Number) -> Expr:
        return self + (-other)

    def __rsub__(self, other: Expr | Number) -> Expr:
        return (-self) + other

    def __mul__(self, scalar: Number) -> Expr:
        return Expr({i: c * scalar for i, c in self.terms.items()}, self.const * scalar)

    __rmul__ = __mul__

    def value(self, x: Sequence[float]) -> float:
        return sum(c * x[i] for i, c in self.terms.items()) + self.const


def as_expr(item: Expr | Number) -> Expr:
    return item if isinstance(item, Expr) else Expr({}, float(item))


@dataclass(frozen=True, slots=True)
class Row:
    name: str
    terms: Mapping[int, float]
    lower: float
    upper: float
    kind: str
    shared: bool
    meta: Mapping[str, str]


@dataclass(slots=True)
class Column:
    name: str
    lower: float
    upper: float
    integer: bool


Sense = Literal["max", "min"]


class LinearModel:
    def __init__(self, sense: Sense) -> None:
        self.sense = sense
        self.columns: list[Column] = []
        self.rows: list[Row] = []
        self.objective: Expr = Expr()
        self._row_names: set[str] = set()

    def __repr__(self) -> str:
        return f"LinearModel(sense={self.sense!r}, cols={len(self.columns)}, rows={len(self.rows)})"

    def var(self, name: str, lower: float = 0.0, upper: float = INF, *, integer: bool = False) -> Expr:
        self.columns.append(Column(name, lower, upper, integer))
        return Expr({len(self.columns) - 1: 1.0})

    def binary(self, name: str) -> Expr:
        return self.var(name, 0.0, 1.0, integer=True)

    def add(
        self,
        name: str,
        expr: Expr | Number,
        *,
        lower: float = -INF,
        upper: float = INF,
        kind: str,
        shared: bool = True,
        meta: Mapping[str, str] | None = None,
    ) -> None:
        if name in self._row_names:
            raise ValueError(f"duplicate row name {name!r}")
        expr = as_expr(expr)
        terms = {i: c for i, c in expr.terms.items() if c != 0.0}
        self._row_names.add(name)
        self.rows.append(Row(name, terms, lower - expr.const, upper - expr.const, kind, shared, dict(meta or {})))

    def le(self, name: str, lhs: Expr | Number, rhs: Expr | Number, **tags) -> None:
        self.add(name, as_expr(lhs) - rhs, upper=0.0, **tags)

    def ge(self, name: str, lhs: Expr | Number, rhs: Expr | Number, **tags) -> None:
        self.add(name, as_expr(lhs) - rhs, lower=0.0, **tags)

    def eq(self, name: str, lhs: Expr | Number, rhs: Expr | Number, **tags) -> None:
        self.add(name, as_expr(lhs) - rhs, lower=0.0, upper=0.0, **tags)

    @property
    def is_mip(self) -> bool:
        return any(c.integer for c in self.columns)

    def solve(self, *, mip_rel_gap: float = 1e-6) -> Solution:
        h = highspy.Highs()
        for option, value in {
            "output_flag": False,
            "threads": 1,
            "random_seed": 0,
            "mip_rel_gap": mip_rel_gap,
        }.items():
            h.setOptionValue(option, value)
        h.passModel(self._to_highs())
        h.run()
        status = h.modelStatusToString(h.getModelStatus())
        solution = h.getSolution()
        info = h.getInfo()
        x = np.array(solution.col_value) if solution.value_valid else np.full(len(self.columns), np.nan)
        dual_ok = solution.dual_valid and not self.is_mip
        return Solution(
            model=self,
            status=status,
            objective=info.objective_function_value + self.objective.const,
            x=x,
            row_dual=np.array(solution.row_dual) if dual_ok else None,
            col_dual=np.array(solution.col_dual) if dual_ok else None,
        )

    def _to_highs(self) -> highspy.HighsLp:
        n_cols = len(self.columns)
        by_col: list[list[tuple[int, float]]] = [[] for _ in range(n_cols)]
        for r, row in enumerate(self.rows):
            for c, coef in row.terms.items():
                by_col[c].append((r, coef))
        starts, index, values = [0], [], []
        for entries in by_col:
            for r, coef in entries:
                index.append(r)
                values.append(coef)
            starts.append(len(index))

        lp = highspy.HighsLp()
        lp.num_col_ = n_cols
        lp.num_row_ = len(self.rows)
        cost = np.zeros(n_cols)
        for i, coef in self.objective.terms.items():
            cost[i] = coef
        lp.col_cost_ = cost
        lp.col_lower_ = np.array([c.lower for c in self.columns])
        lp.col_upper_ = np.array([c.upper for c in self.columns])
        lp.row_lower_ = np.array([r.lower for r in self.rows])
        lp.row_upper_ = np.array([r.upper for r in self.rows])
        lp.a_matrix_.format_ = highspy.MatrixFormat.kColwise
        lp.a_matrix_.start_ = np.array(starts, dtype=np.int32)
        lp.a_matrix_.index_ = np.array(index, dtype=np.int32)
        lp.a_matrix_.value_ = np.array(values)
        lp.sense_ = highspy.ObjSense.kMaximize if self.sense == "max" else highspy.ObjSense.kMinimize
        if self.is_mip:
            lp.integrality_ = [
                highspy.HighsVarType.kInteger if c.integer else highspy.HighsVarType.kContinuous for c in self.columns
            ]
        return lp


@dataclass(frozen=True, slots=True)
class Solution:
    """Primal values, and for LPs the row duals.

    For a maximisation HiGHS reports ``row_dual`` as d(objective)/d(row bound) and
    ``col_dual = cost - A^T row_dual`` (reduced cost); tests pin this convention.
    """

    model: LinearModel
    status: str
    objective: float
    x: np.ndarray
    row_dual: np.ndarray | None
    col_dual: np.ndarray | None

    @property
    def optimal(self) -> bool:
        return self.status == "Optimal"

    def value(self, item: Expr | Number) -> float:
        return as_expr(item).value(self.x)

    def dual(self, row_name: str) -> float:
        if self.row_dual is None:
            raise ValueError("duals exist only for an optimal LP solve")
        index = next(i for i, r in enumerate(self.model.rows) if r.name == row_name)
        return float(self.row_dual[index])
