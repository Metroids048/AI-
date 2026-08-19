"""Research-only vectorbt subprocess adapter."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .contracts import ResearchExperimentResult, ResearchExperimentSpec
from .subprocess_runner import run_research_subprocess

_ROOT = Path(__file__).resolve().parents[3]
Runner = Callable[..., Any]


class VectorbtScreenAdapter:
    engine = "vectorbt"
    engine_sha = "34b6d5935e3ea3eccd549e2592bc0f455b8045f5"

    def __init__(
        self,
        *,
        python_executable: str | None = None,
        runner: Runner = run_research_subprocess,
    ) -> None:
        self.python_executable = (
            python_executable
            or os.getenv("VECTORBT_PYTHON")
            or _isolated_vectorbt_python()
            or shutil.which("agent-python")
        )
        self.runner = runner

    def health(self) -> dict[str, Any]:
        if not self.python_executable:
            return {
                "engine": self.engine,
                "sha": self.engine_sha,
                "available": False,
                "mode": "external_subprocess_research_engine",
            }
        probe = self.runner(
            [self.python_executable, "-c", "import vectorbt"],
            timeout_seconds=20,
            cwd=_ROOT,
        )
        return {
            "engine": self.engine,
            "sha": self.engine_sha,
            "available": probe.returncode == 0,
            "mode": "external_subprocess_research_engine",
        }

    def screen(
        self, spec: ResearchExperimentSpec, rows: list[dict[str, Any]], *, run_id: str, top_n: int = 10
    ) -> ResearchExperimentResult:
        fields = self._result_fields(spec, run_id)
        if not self.health()["available"]:
            return ResearchExperimentResult(
                status="unavailable",
                failure_reason="VECTORBT_UNAVAILABLE",
                provenance={"external_process": False, "credentials": False},
                **fields,
            )
        with tempfile.TemporaryDirectory(prefix="quant-vectorbt-") as directory:
            input_path = Path(directory) / "input.json"
            input_path.write_text(
                json.dumps(
                    {
                        "rows": rows,
                        "parameter_space": spec.parameter_space,
                        "cost_model": spec.cost_model,
                        "engine_options": spec.engine_options,
                        "top_n": top_n,
                    },
                    ensure_ascii=True,
                ),
                encoding="utf-8",
            )
            completed = self.runner(
                [
                    self.python_executable,
                    "-m",
                    "services.research.integrations.vectorbt_subprocess",
                    "--input",
                    str(input_path),
                ],
                cwd=_ROOT,
            )
        if completed.returncode != 0:
            return ResearchExperimentResult(
                status="failed",
                failure_reason=_failure_reason(completed),
                provenance={"external_process": True, "credentials": False},
                **fields,
            )
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError:
            return ResearchExperimentResult(
                status="failed",
                failure_reason="VECTORBT_INVALID_RESULT",
                provenance={"external_process": True, "credentials": False},
                **fields,
            )
        return ResearchExperimentResult(
            status="completed",
            provenance={"external_process": True, "credentials": False},
            **payload,
            **fields,
        )

    def _result_fields(self, spec: ResearchExperimentSpec, run_id: str) -> dict[str, Any]:
        return {
            "run_id": run_id,
            "engine": self.engine,
            "engine_sha": self.engine_sha,
            "input_spec_hash": spec.input_spec_hash,
            "dataset_hash": spec.dataset_hash,
            "cost_model_hash": spec.cost_model_hash,
            "strategy_hash": spec.strategy_hash,
        }


def _failure_reason(completed: Any) -> str:
    detail = (completed.stderr or completed.stdout or "").strip().splitlines()
    return detail[-1][:500] if detail else "VECTORBT_SUBPROCESS_FAILED"


def _isolated_vectorbt_python() -> str | None:
    candidate = _ROOT / ".local" / "research-engines" / "vectorbt" / "Scripts" / "python.exe"
    return str(candidate) if candidate.is_file() else None
