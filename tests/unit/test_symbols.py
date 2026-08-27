import pytest

from gts_agent.core.abi.elf import DynamicSymbol
from gts_agent.core.abi.symbols import (
    CompatibilityError,
    check_nonshared_coverage,
    require_nonshared_complete,
    versioned_symbol_set,
)


def _sym(name, version=None, defined=True, binding="GLOBAL",
         visibility="DEFAULT", symbol_type="FUNC"):
    return DynamicSymbol(
        name=name, version=version, defined=defined,
        binding=binding, symbol_type=symbol_type, visibility=visibility,
    )


def test_versioned_symbol_set_filters():
    symbols = [
        _sym("f1", "GLIBCXX_3.4"),
        _sym("f2", "GLIBCXX_3.4.30"),
        _sym("undefined", "GLIBCXX_3.4", defined=False),
        _sym("hidden", "GLIBCXX_3.4", visibility="HIDDEN"),
        _sym("local", "GLIBCXX_3.4", binding="LOCAL"),
        _sym("weak", "GLIBCXX_3.4", binding="WEAK"),
    ]
    result = versioned_symbol_set(symbols)
    assert ("f1", "GLIBCXX_3.4") in result
    assert ("weak", "GLIBCXX_3.4") in result
    assert all(name not in ("undefined", "hidden", "local") for name, _ in result)


def test_nonshared_coverage_complete():
    system = {("old_fn", "GLIBCXX_3.4")}
    target = {("old_fn", "GLIBCXX_3.4"), ("new_fn", "GLIBCXX_3.4.33")}
    nonshared = {("new_fn", "")}
    result = check_nonshared_coverage(system, target, nonshared)
    assert result.complete
    assert result.target_delta == {("new_fn", "GLIBCXX_3.4.33")}
    require_nonshared_complete(result)  # 不抛异常


def test_nonshared_coverage_incomplete_blocks():
    system = {("old_fn", "GLIBCXX_3.4")}
    target = {
        ("old_fn", "GLIBCXX_3.4"),
        ("new_fn", "GLIBCXX_3.4.33"),
        ("another_new", "GLIBCXX_3.4.33"),
    }
    nonshared = {("new_fn", "")}
    result = check_nonshared_coverage(system, target, nonshared)
    assert not result.complete
    assert result.missing == {("another_new", "GLIBCXX_3.4.33")}
    with pytest.raises(CompatibilityError) as exc:
        require_nonshared_complete(result)
    assert exc.value.code == "E-NONSHARED-INCOMPLETE"
    assert "another_new@GLIBCXX_3.4.33" in exc.value.missing
