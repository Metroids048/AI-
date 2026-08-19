"""Research-only Freqtrade validator; it never invokes ``freqtrade trade``."""

from __future__ import annotations

import json
import shutil
import tempfile
import threading
from collections.abc import Callable
from contextlib import contextmanager
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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
        self.executable = executable or _isolated_freqtrade_python() or shutil.which("freqtrade")
        self.runner = runner

    def health(self) -> dict[str, Any]:
        command = self._command_prefix()
        available = bool(command)
        version = None
        if available:
            try:
                probe = self.runner([*command, "--version"], timeout_seconds=20, cwd=_ROOT)
                available = probe.returncode == 0
                version = (probe.stdout or probe.stderr or "").strip().splitlines()[-1] if available else None
            except (OSError, TimeoutError):
                available = False
        return {
            "engine": self.engine,
            "sha": self.engine_sha,
            "available": available,
            "version": version,
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
        command_prefix = self._command_prefix()
        if not command_prefix:
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
            with _offline_market_config(config, dataset.get("rows", [])) as research_config:
                common = [
                    "--config",
                    str(research_config),
                    "--strategy",
                    strategy,
                    "--datadir",
                    str(data_dir),
                    "--data-format",
                    "json",
                    "--timerange",
                    str(options.get("timerange") or _timerange(dataset.get("rows", []))),
                ]
                epochs = str(min(max(int(options.get("hyperopt_epochs", 10)), 1), 100))
                commands = [
                    [*command_prefix, "backtesting", *common],
                    [
                        *command_prefix,
                        "hyperopt",
                        *common,
                        "--epochs",
                        epochs,
                        "--hyperopt-loss",
                        str(options.get("hyperopt_loss") or "SharpeHyperOptLossDaily"),
                    ],
                    [*command_prefix, "lookahead-analysis", *common],
                    [*command_prefix, "recursive-analysis", *common],
                ]
                completed_names: list[str] = []
                for command in commands:
                    subcommand = command[len(command_prefix)]
                    if subcommand not in _ALLOWED_COMMANDS:
                        raise RuntimeError("research adapter cannot invoke a trading command")
                    completed = self.runner(command, cwd=_ROOT)
                    completed_names.append(subcommand)
                    if completed.returncode != 0:
                        return ResearchExperimentResult(
                            status="failed",
                            failure_reason=_failure_reason(completed),
                            provenance={
                                **provenance,
                                "external_process": True,
                                "completed_commands": completed_names,
                            },
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

    def _command_prefix(self) -> list[str]:
        if not self.executable:
            return []
        if Path(self.executable).name.lower().startswith("python"):
            return [self.executable, "-m", "freqtrade"]
        return [self.executable]

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


def _timerange(rows: list[dict[str, Any]]) -> str:
    timestamps: list[datetime] = []
    for row in rows:
        value = row.get("timestamp")
        if isinstance(value, str):
            timestamps.append(datetime.fromisoformat(value.replace("Z", "+00:00")))
        elif value is not None:
            timestamps.append(datetime.fromtimestamp(float(value) / 1000, tz=datetime.now().astimezone().tzinfo))
    if not timestamps:
        raise ValueError("canonical dataset has no timestamps")
    start = min(timestamps).strftime("%Y%m%d")
    end = max(timestamps).date().toordinal() + 1
    end_date = datetime.fromordinal(end).strftime("%Y%m%d")
    return f"{start}-{end_date}"


def _isolated_freqtrade_python() -> str | None:
    candidate = _ROOT / ".local" / "research-engines" / "freqtrade" / "Scripts" / "python.exe"
    return str(candidate) if candidate.is_file() else None


@contextmanager
def _offline_market_config(config_path: Path, rows: list[dict[str, Any]]):
    """Route CCXT market metadata to a local deterministic provider."""
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    symbols = sorted({str(row.get("symbol", "")).split(":", 1)[0] for row in rows if row.get("symbol")})
    server = ThreadingHTTPServer(("127.0.0.1", 0), _market_handler(symbols))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    exchange = payload.setdefault("exchange", {})
    ccxt_config = exchange.setdefault("ccxt_config", {})
    api_urls = ccxt_config.setdefault("urls", {}).setdefault("api", {})
    api_urls.update(
        {
            "public": f"{base}/api/v3",
            "private": f"{base}/api/v3",
            "fapiPublic": f"{base}/fapi/v1",
            "fapiPrivate": f"{base}/fapi/v1",
            "dapiPublic": f"{base}/dapi/v1",
            "dapiPrivate": f"{base}/dapi/v1",
        }
    )
    ccxt_config.setdefault("options", {})["fetchMarkets"] = {"types": ["spot"]}
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", prefix="quant-freqtrade-config-", delete=False, encoding="utf-8"
    ) as handle:
        json.dump(payload, handle)
        temporary_path = Path(handle.name)
    try:
        yield temporary_path
    finally:
        temporary_path.unlink(missing_ok=True)
        server.shutdown()


def _market_handler(symbols: list[str]):
    market_symbols = []
    for pair in symbols:
        base, quote = pair.replace("_", "/").split("/", 1)
        market_symbols.append(
            {
                "symbol": f"{base}{quote}",
                "status": "TRADING",
                "baseAsset": base,
                "baseAssetPrecision": 8,
                "quoteAsset": quote,
                "quotePrecision": 8,
                "quoteAssetPrecision": 8,
                "baseCommissionPrecision": 8,
                "quoteCommissionPrecision": 8,
                "orderTypes": ["LIMIT", "MARKET"],
                "icebergAllowed": True,
                "ocoAllowed": True,
                "quoteOrderQtyMarketAllowed": True,
                "isSpotTradingAllowed": True,
                "isMarginTradingAllowed": True,
                "filters": [
                    {
                        "filterType": "PRICE_FILTER",
                        "minPrice": "0.00000001",
                        "maxPrice": "1000000000",
                        "tickSize": "0.00000001",
                    },
                    {
                        "filterType": "LOT_SIZE",
                        "minQty": "0.000001",
                        "maxQty": "1000000",
                        "stepSize": "0.000001",
                    },
                    {"filterType": "MIN_NOTIONAL", "minNotional": "1"},
                ],
            }
        )
    body = json.dumps({"timezone": "UTC", "serverTime": 1760000000000, "symbols": market_symbols}).encode()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if self.path.endswith("/exchangeInfo"):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path.endswith("/ping"):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"{}")
                return
            self.send_response(404)
            self.end_headers()

        def log_message(self, *_args):
            return

    return Handler
