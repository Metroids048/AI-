"""Research-only Freqtrade validator; it never invokes ``freqtrade trade``."""

from __future__ import annotations

import json
import shutil
import tempfile
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from .contracts import ResearchExperimentResult, ResearchExperimentSpec
from .dataset_export import load_canonical_dataset
from .subprocess_runner import run_research_subprocess

_ROOT = Path(__file__).resolve().parents[3]
Runner = Callable[..., Any]
_ALLOWED_COMMANDS = ("backtesting", "hyperopt", "lookahead-analysis", "recursive-analysis")


class FreqtradeValidationAdapter:
    engine = "freqtrade"
    engine_sha = "b3404c9d81422fed6a8fd83d3d296c37c7915327"

    def __init__(
        self,
        *,
        executable: str | None = None,
        runner: Runner = run_research_subprocess,
    ) -> None:
        self.executable = executable or shutil.which("freqtrade")
        self.runner = runner

    def health(self) -> dict[str, Any]:
        return {
            "engine": self.engine,
            "sha": self.engine_sha,
            "available": bool(self.executable),
            "mode": "external_subprocess_validator",
            "trade_runtime": False,
        }

    def validate(
        self,
        spec: ResearchExperimentSpec,
        rows: list[dict[str, Any]],
        *,
        run_id: str,
        candidate: dict[str, Any] | None = None,
    ) -> ResearchExperimentResult:
        fields = self._result_fields(spec, run_id)
        provenance = {
            "external_process": False,
            "commands_allowed": list(_ALLOWED_COMMANDS),
            "trade_command_allowed": False,
        }
        if not self.executable:
            return ResearchExperimentResult(
                status="unavailable", failure_reason="FREQTRADE_UNAVAILABLE", provenance=provenance, **fields
            )
        raw_options = spec.engine_options.get("freqtrade")
        options: dict[str, Any] = raw_options if isinstance(raw_options, dict) else {}
        config = Path(str(options.get("config_path") or ""))
        strategy = str(options.get("strategy") or "")
        dataset_path = Path(str(options.get("canonical_dataset_path") or ""))
        if not config.is_file() or not dataset_path.is_file() or not strategy:
            return ResearchExperimentResult(
                status="failed", failure_reason="FREQTRADE_CONFIGURATION_REQUIRED", provenance=provenance, **fields
            )
        try:
            dataset = load_canonical_dataset(dataset_path)
            _assert_research_config_has_no_credentials(config)
        except (OSError, ValueError) as exc:
            return ResearchExperimentResult(
                status="failed",
                failure_reason=f"FREQTRADE_RESEARCH_INPUT_INVALID: {exc}",
                provenance=provenance,
                **fields,
            )
        with tempfile.TemporaryDirectory(prefix="quant-freqtrade-data-") as directory:
            data_dir = Path(directory)
            _write_freqtrade_json_data(dataset.get("rows", []), data_dir)
            common = [
                "--config",
                str(config),
                "--strategy",
                strategy,
                "--datadir",
                str(data_dir),
                "--data-format",
                "json",
            ]
            epochs = str(min(max(int(options.get("hyperopt_epochs", 10)), 1), 100))
            commands = [
                [self.executable, "backtesting", *common],
                [self.executable, "hyperopt", *common, "--epochs", epochs],
                [self.executable, "lookahead-analysis", *common],
                [self.executable, "recursive-analysis", *common],
            ]
            completed_names: list[str] = []
            for command in commands:
                if command[1] not in _ALLOWED_COMMANDS:
                    raise RuntimeError("research adapter cannot invoke a trading command")
                completed = self.runner(command, cwd=_ROOT)
                completed_names.append(command[1])
                if completed.returncode != 0:
                    return ResearchExperimentResult(
                        status="failed",
                        failure_reason=_failure_reason(completed),
                        provenance={**provenance, "external_process": True, "completed_commands": completed_names},
                        **fields,
                    )
        return ResearchExperimentResult(
            status="completed",
            parameter_plateau={"input_candidate": candidate or {}},
            lookahead_status="PASS",
            recursive_status="PASS",
            provenance={**provenance, "external_process": True, "completed_commands": completed_names},
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
    return detail[-1][:500] if detail else "FREQTRADE_SUBPROCESS_FAILED"


def _assert_research_config_has_no_credentials(config_path: Path) -> None:
    payload = json.loads(config_path.read_text(encoding="utf-8"))

    def visit(value: Any, path: str = "config") -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                credential_markers = ("API_KEY", "API_SECRET", "SECRET_KEY", "PASSWORD", "TOKEN")
                if any(marker in str(key).upper() for marker in credential_markers):
                    raise ValueError(f"research config contains credential-shaped field: {path}.{key}")
                visit(child, f"{path}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, f"{path}[{index}]")

    visit(payload)


def _write_freqtrade_json_data(rows: list[dict[str, Any]], data_dir: Path) -> None:
    grouped: dict[tuple[str, str], list[list[Any]]] = {}
    for row in rows:
        required = ("symbol", "timeframe", "open", "high", "low", "close", "volume", "timestamp")
        if any(field not in row for field in required):
            raise ValueError("canonical dataset requires symbol/timeframe/timestamp/OHLCV for Freqtrade")
        timestamp = row["timestamp"]
        if isinstance(timestamp, str):
            timestamp = int(datetime.fromisoformat(timestamp.replace("Z", "+00:00")).timestamp() * 1000)
        timestamp = int(timestamp)
        if timestamp < 10_000_000_000:
            timestamp *= 1000
        symbol = str(row["symbol"]).replace("/", "_")
        timeframe = str(row["timeframe"])
        grouped.setdefault((symbol, timeframe), []).append(
            [timestamp, row["open"], row["high"], row["low"], row["close"], row["volume"]]
        )
    if not grouped:
        raise ValueError("canonical dataset has no OHLCV rows")
    for (symbol, timeframe), candles in grouped.items():
        candles.sort(key=lambda candle: candle[0])
        (data_dir / f"{symbol}-{timeframe}.json").write_text(json.dumps(candles), encoding="utf-8")
