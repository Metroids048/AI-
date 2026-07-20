from __future__ import annotations

import builtins
import runpy
from pathlib import Path


def test_adapter_treats_binary_loader_error_as_unavailable(monkeypatch) -> None:
    real_import = builtins.__import__

    def import_with_broken_optional_binary(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "pandas_ta":
            raise OSError("llvmlite.dll could not be loaded")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", import_with_broken_optional_binary)
    adapter_path = Path(__file__).parents[1] / "services" / "strategy_library" / "technical" / "pandas_ta_adapter.py"

    namespace = runpy.run_path(str(adapter_path))

    assert namespace["ta"] is None
