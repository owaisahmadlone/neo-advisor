from __future__ import annotations

import numpy as np
import pytest

from neo.opt.lp import Expr, LinearModel


def test_dual_sign_convention_for_maximisation():
    m = LinearModel("max")
    x, y = m.var("x"), m.var("y")
    m.le("total", x + y, 4.0, kind="test")
    m.le("x_cap", x, 3.0, kind="test")
    m.objective = x * 3 + y * 2
    sol = m.solve()
    assert sol.optimal and sol.objective == pytest.approx(11.0)
    assert sol.dual("total") == pytest.approx(2.0)  # d obj / d rhs
    assert sol.dual("x_cap") == pytest.approx(1.0)


def test_reduced_cost_identity():
    m = LinearModel("max")
    x, y, z = m.var("x"), m.var("y"), m.var("z", 0, 5)
    m.le("a", x + y * 2 + z, 10.0, kind="test")
    m.ge("b", x - y, -2.0, kind="test")
    m.objective = x * 1 + y * 1.5 - z * 0.2
    sol = m.solve()
    a = np.zeros((len(m.rows), len(m.columns)))
    for r, row in enumerate(m.rows):
        for c, coef in row.terms.items():
            a[r, c] = coef
    cost = np.array([m.objective.terms.get(i, 0.0) for i in range(len(m.columns))])
    assert np.allclose(sol.col_dual, cost - a.T @ sol.row_dual)


def test_mip_has_no_duals_and_constants_survive():
    m = LinearModel("min")
    b = m.binary("b")
    x = m.var("x", 0, 10)
    m.ge("need", x + b * 5, 7.0, kind="test")
    m.objective = x + b * 1 + 100.0
    sol = m.solve()
    assert sol.optimal and sol.row_dual is None
    assert sol.objective == pytest.approx(103.0)


def test_expr_algebra():
    m = LinearModel("max")
    x = m.var("x")
    e = Expr.total([x * 2, 3, -x, 1.5])
    assert e.terms == {0: 1.0} and e.const == 4.5
    assert (5 - x).const == 5 and (5 - x).terms == {0: -1.0}
