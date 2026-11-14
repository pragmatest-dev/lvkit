"""A default given to a variable just before it is given its value is removed
(``remove_dead_defaults``); every case that could still read the default stays."""

from __future__ import annotations

import ast
import textwrap
from typing import Any

from lvkit.codegen.ast_optimizer import remove_dead_defaults


def _clean(code: str) -> str:
    module = ast.parse(textwrap.dedent(code))
    return ast.unparse(ast.fix_missing_locations(remove_dead_defaults(module)))


def _unchanged(code: str) -> None:
    assert _clean(code) == ast.unparse(ast.parse(textwrap.dedent(code)))


def _run(source: str, name: str, *args: Any) -> Any:
    namespace: dict[str, Any] = {}
    exec(compile(source, "<test>", "exec"), namespace)
    return namespace[name](*args)


# -- 1a: overwritten at once ---------------------------------------------------


def test_a_default_overwritten_by_the_next_statement_is_removed():
    code = """
    def f(error_in):
        error_out = None
        error_out = error_in
        return error_out
    """
    assert _clean(code) == (
        "def f(error_in):\n    error_out = error_in\n    return error_out"
    )
    assert _run(_clean(code), "f", 7) == _run(textwrap.dedent(code), "f", 7) == 7


def test_an_empty_list_default_is_removed():
    code = """
    def f(a):
        rows = []
        rows = [a]
        return rows
    """
    assert _clean(code) == "def f(a):\n    rows = [a]\n    return rows"


def test_a_default_in_a_loop_body_is_removed():
    code = """
    def f(n):
        for i in range(n):
            x = 0
            x = i
        return x
    """
    assert _clean(code) == (
        "def f(n):\n    for i in range(n):\n        x = i\n    return x"
    )


def test_a_default_overwritten_by_a_call_is_removed_but_a_default_with_a_call_is_not():
    _unchanged(
        """
        def f():
            x = g()
            x = 1
            return x
        """
    )


def test_the_default_is_kept_when_the_overwrite_is_two_statements_later():
    _unchanged(
        """
        def f(a):
            x = None
            y = a
            x = y
            return x
        """
    )


def test_the_default_is_kept_when_something_reads_it_in_between():
    _unchanged(
        """
        def f(a):
            x = None
            y = x
            x = a
            return (x, y)
        """
    )


def test_a_global_name_is_never_touched():
    _unchanged(
        """
        def f(a):
            global x
            x = None
            x = a
        """
    )


def test_a_name_read_in_a_nested_function_is_never_touched():
    _unchanged(
        """
        def f(a):
            def g():
                return x
            x = None
            x = a
            return g
        """
    )


def test_a_default_inside_a_try_is_kept():
    _unchanged(
        """
        def f(a):
            try:
                x = None
                x = a()
            except ValueError:
                return x
            return x
        """
    )


def test_an_assignment_with_two_targets_or_a_non_name_target_is_never_touched():
    _unchanged(
        """
        def f(a, d):
            x = y = None
            x = a
            d[0] = None
            d[0] = a
            return (x, y, d)
        """
    )


# -- 1b: the default takes the place of the one read -----------------------------


def test_a_default_read_once_by_the_overwrite_takes_the_place_of_the_read():
    code = """
    def f(live_io):
        quit_program = False
        quit_program = live_io.read(1, quit_program)
        return quit_program
    """
    assert _clean(code) == (
        "def f(live_io):\n    quit_program = live_io.read(1, False)\n"
        "    return quit_program"
    )

    class Live:
        def read(self, key, value):
            return (key, value)

    assert _run(_clean(code), "f", Live()) == _run(textwrap.dedent(code), "f", Live())


def test_the_default_is_kept_when_the_overwrite_reads_it_twice():
    _unchanged(
        """
        def f(a):
            x = 0
            x = x + a + x
            return x
        """
    )


def test_the_default_is_kept_when_the_one_read_is_in_a_comprehension():
    _unchanged(
        """
        def f(a):
            x = []
            x = [x for _ in a]
            return x
        """
    )


# -- 1c: every branch assigns before reading -------------------------------------


def test_a_default_is_removed_when_every_branch_of_an_if_else_assigns_it():
    code = """
    def f(flag, a):
        out = None
        if flag:
            out = a
        else:
            out = 0
        return out
    """
    assert _clean(code) == (
        "def f(flag, a):\n    if flag:\n        out = a\n    else:\n        out = 0\n"
        "    return out"
    )
    for flag in (True, False):
        assert _run(_clean(code), "f", flag, 5) == _run(
            textwrap.dedent(code), "f", flag, 5
        )


def test_a_default_is_removed_through_an_elif_chain_ending_in_else():
    code = """
    def f(k):
        out = 0
        if k == 1:
            out = 10
        elif k == 2:
            log = k
            out = 20
        else:
            out = 30
        return out
    """
    assert "out = 0" not in _clean(code)
    for k in (1, 2, 3):
        assert _run(_clean(code), "f", k) == _run(textwrap.dedent(code), "f", k)


def test_a_branch_that_raises_before_reading_counts_as_assigning():
    code = """
    def f(flag):
        out = None
        if flag:
            out = 1
        else:
            raise ValueError('no')
        return out
    """
    assert "out = None" not in _clean(code)


def test_the_default_is_kept_for_an_if_without_else():
    _unchanged(
        """
        def f(flag, a):
            out = None
            if flag:
                out = a
            return out
        """
    )


def test_the_default_is_kept_for_an_elif_chain_without_else():
    _unchanged(
        """
        def f(k):
            out = 0
            if k == 1:
                out = 1
            elif k == 2:
                out = 2
            return out
        """
    )


def test_the_default_is_kept_when_a_branch_reads_it_before_assigning():
    _unchanged(
        """
        def f(flag, a):
            out = 0
            if flag:
                out = a
            else:
                b = out
                out = b + 1
            return out
        """
    )


def test_the_default_is_kept_when_a_branch_can_break_before_assigning():
    code = """
    def f(items):
        for item in items:
            out = 0
            if item > 0:
                if item > 5:
                    break
                out = item
            else:
                out = -1
        return out
    """
    _unchanged(code)
    # The break path leaves out at its default for this iteration.
    assert _run(_clean(code), "f", [3, 9]) == 0


def test_a_break_of_a_loop_inside_the_branch_does_not_keep_the_default():
    code = """
    def f(flag, items):
        out = 0
        if flag:
            for item in items:
                if item > 5:
                    break
            out = 1
        else:
            out = 2
        return out
    """
    assert "out = 0" not in _clean(code)
    assert (
        _run(_clean(code), "f", True, [9])
        == _run(textwrap.dedent(code), "f", True, [9])
        == 1
    )


def test_the_default_is_kept_when_a_branch_can_continue_before_assigning():
    _unchanged(
        """
        def f(items):
            for item in items:
                out = 0
                if item:
                    if item > 5:
                        continue
                    out = item
                else:
                    out = -1
            return out
        """
    )


def test_the_default_is_kept_when_a_branch_adds_to_it_before_assigning():
    code = """
    def f(flag):
        out = 0
        if flag:
            out += 1
            out = 2
        else:
            out = 3
        return out
    """
    _unchanged(code)
    # Without the default, ``out += 1`` would raise UnboundLocalError.
    assert _run(_clean(code), "f", True) == 2


def test_the_default_is_kept_when_a_branch_assigns_from_itself():
    _unchanged(
        """
        def f(flag):
            out = 0
            if flag:
                out = 1
            else:
                out = out + 1
            return out
        """
    )


def test_the_default_is_kept_when_a_test_reads_it():
    _unchanged(
        """
        def f(a):
            out = 0
            if out == a:
                out = 1
            else:
                out = 2
            return out
        """
    )


def test_a_default_is_removed_when_every_case_of_a_match_assigns_it():
    code = """
    def f(k):
        out = None
        match k:
            case 1:
                out = 'one'
            case _:
                out = 'other'
        return out
    """
    assert "out = None" not in _clean(code)
    for k in (1, 2):
        assert _run(_clean(code), "f", k) == _run(textwrap.dedent(code), "f", k)


def test_the_default_is_kept_for_a_match_without_a_final_wildcard_case():
    _unchanged(
        """
        def f(k):
            out = None
            match k:
                case 1:
                    out = 'one'
                case 2:
                    out = 'two'
            return out
        """
    )


def test_the_default_is_kept_for_a_match_whose_case_reads_it_first():
    _unchanged(
        """
        def f(k):
            out = 0
            match k:
                case 1:
                    out = out + 1
                case _:
                    out = 2
            return out
        """
    )
