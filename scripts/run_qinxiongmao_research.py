"""Audit and (only when mechanically specified) replay Qinxiongmao strategies.

This is a research-only adapter. It consumes the Video repository's evidence,
reuses AI-'s canonical technical replay service, and never writes execution
manifests or changes validation policy.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from services.data import DataRepository
from services.database import get_session_factory
from services.validation.policy import default_policy

REQUIRED = ("entry", "exit", "stop_loss", "timeframe", "market_regime", "position_sizing")
TIMEFRAME_MAP = {
    "15分钟": "15m",
    "30分钟": "30m",
    "1小时": "1h",
    "4小时": "4h",
    "日线": "1d",
    "周线": "1w",
    "10分钟": "10m",
}
AMBIGUOUS_MARKERS = ("等等", "我不知道", "不确定", "猜想", "观望", "不能单独作为交易系统", "如果说", "大概")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _git_rev(path: Path) -> str | None:
    try:
        return subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _source_audit(spec: dict[str, Any], rules: dict[str, dict[str, Any]]) -> dict[str, Any]:
    failures: list[str] = []
    source_rules = [str(x) for x in spec.get("source_rules", [])]
    evidence = [rules.get(rule_id) for rule_id in source_rules]
    if not source_rules or not spec.get("source_videos"):
        failures.append("missing_source_identity")
    if any(item is None for item in evidence):
        failures.append("source_rule_not_found")
    if spec.get("source_provenance") != "SPEAKER_EXPLICIT":
        failures.append("source_provenance_not_explicit")
    if not any(item and item.get("source_timestamps") for item in evidence):
        failures.append("missing_timestamp_evidence")
    if spec.get("assumptions"):
        failures.append("assumptions_present")
    return {"passed": not failures, "failures": failures}


def _mechanizability_audit(spec: dict[str, Any]) -> dict[str, Any]:
    failures: list[str] = []
    values: dict[str, Any] = {
        "entry": (spec.get("entry_long") or {}).get("condition"),
        "exit": (spec.get("exit") or {}).get("condition"),
        "stop_loss": (spec.get("stop_loss") or {}).get("condition"),
        "timeframe": spec.get("timeframes"),
        "market_regime": (spec.get("regime_filter") or {}).get("value"),
        "position_sizing": (spec.get("position_sizing") or {}).get("condition"),
    }
    for field in REQUIRED:
        if not values.get(field):
            failures.append(f"missing_{field}")
    if not isinstance(values["entry"], dict):
        failures.append("entry_not_structured")
    if not isinstance(values["exit"], dict):
        failures.append("exit_not_structured")
    if not isinstance(values["stop_loss"], dict):
        failures.append("stop_loss_not_structured")
    if not isinstance(values["position_sizing"], dict):
        failures.append("position_sizing_not_structured")
    for field in ("entry", "exit", "stop_loss", "position_sizing"):
        value = values[field]
        if isinstance(value, str) and (len(value) > 600 or any(marker in value for marker in AMBIGUOUS_MARKERS)):
            failures.append(f"{field}_contains_ambiguous_prose")
    timeframes = values["timeframe"] if isinstance(values["timeframe"], list) else []
    if any(str(tf) not in TIMEFRAME_MAP for tf in timeframes):
        failures.append("timeframe_not_normalized")
    if not spec.get("instrument") and not spec.get("instrument_scope"):
        failures.append("instrument_not_explicit")
    return {"passed": not failures, "failures": failures}


def _fragment_accounting(incomplete: list[dict[str, Any]]) -> dict[str, Any]:
    """Account for every fragment without silently turning topical overlap into a rule."""
    groups: dict[str, list[str]] = {}
    keywords = ("顺势回调", "三部曲", "军线", "道氏", "谐波", "OB", "FVG", "回测")
    for row in incomplete:
        text = str(row.get("name", "")) + str(row.get("entry", ""))
        hits = [key for key in keywords if key.lower() in text.lower()]
        if hits:
            groups.setdefault("/".join(hits), []).append(str(row.get("rule_id")))
    synthesis_candidates = [
        {
            "topic": topic,
            "rule_ids": ids,
            "source_videos": sorted({video for rid in ids for video in _videos_for_rule(rid, incomplete)}),
        }
        for topic, ids in groups.items()
        if len({video for rid in ids for video in _videos_for_rule(rid, incomplete)}) > 1
    ]
    return {
        "input_incomplete_rules": len(incomplete),
        "accounted_rule_count": len(incomplete),
        "topic_groups": {topic: len(ids) for topic, ids in groups.items()},
        "cross_video_synthesis_candidates": synthesis_candidates,
        "synthesized_count": 0,
        "decision": "NO_SYNTHESIS_WITHOUT_EXPLICIT_AUTHOR_LINK",
    }


def _videos_for_rule(rule_id: str, rows: list[dict[str, Any]]) -> list[str]:
    for row in rows:
        if str(row.get("rule_id")) == rule_id:
            return [str(x) for x in row.get("source_videos", [])]
    return []


def _load_market_data(database_url: str, days: int) -> dict[str, dict[str, list[Any]]]:
    end_at = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    start_at = end_at - timedelta(days=days)
    with get_session_factory(database_url)() as session:
        repo = DataRepository(session)
        return {
            "BTC/USDT": {
                tf: repo.list_ohlcv_bars(symbol="BTC/USDT", timeframe=tf, start_at=start_at, end_at=end_at)
                for tf in ("15m", "1h", "4h")
            }
        }


def _replay_if_mechanizable(spec: dict[str, Any], market_data: dict[str, dict[str, list[Any]]]) -> dict[str, Any]:
    """Replay hook for future structured specs; current text-only specs stop at audit."""
    from services.validation.technical_replay import TechnicalStrategyValidationService
    from shared.models import StrategyContract, StrategyRules, Timeframe

    entry = spec["entry_long"]["condition"]
    timeframe = TIMEFRAME_MAP.get(str((spec.get("timeframes") or ["15分钟"])[0]), "15m")
    strategy = StrategyContract(
        strategy_id=str(spec["strategy_id"]),
        strategy_key=str(spec["strategy_id"]),
        source="qinxiongmao_video",
        core_thesis=str(spec.get("name", "")),
        timeframe=Timeframe(timeframe),
        symbol_scope=["BTC/USDT"],
        rules=StrategyRules(
            entry_rules=entry,
            exit_rules=spec["exit"]["condition"],
            stoploss_rules=spec["stop_loss"]["condition"],
            takeprofit_rules=spec.get("take_profit") or {},
            position_rules=spec["position_sizing"]["condition"],
        ),
    )
    metrics = TechnicalStrategyValidationService(max_workers=1, oos_fraction=0.30, walk_forward_windows=3).replay(
        strategy=strategy, market_data=market_data
    )
    return {"strategy_id": spec["strategy_id"], "status": "REPLAYED", "metrics": metrics.as_dict()}


def run(video_root: Path, database_url: str, days: int, ai_repo_root: Path) -> dict[str, Any]:
    research = video_root / "strategy_research"
    candidates = _read_jsonl(research / "rule_candidates.jsonl")
    incomplete = _read_jsonl(research / "incomplete_rules.jsonl")
    rules = {str(row.get("rule_id")): row for row in candidates}
    specs = [_read_json(path) for path in sorted((research / "strategy_specs").glob("*.json"))]
    audits = []
    replay_results = []
    market_data = None
    for spec in specs:
        source = _source_audit(spec, rules)
        mech = _mechanizability_audit(spec)
        row = {
            "strategy_id": spec.get("strategy_id"),
            "source_audit": source,
            "mechanizability_audit": mech,
            "research_eligible": source["passed"] and mech["passed"],
        }
        if row["research_eligible"]:
            market_data = market_data or _load_market_data(database_url, days)
            replay_results.append(_replay_if_mechanizable(spec, market_data))
        audits.append(row)
    accounting_path = research / "rule-fragment-accounting.jsonl"
    accounting_path.write_text(
        "".join(
            json.dumps(
                {
                    "rule_id": row.get("rule_id"),
                    "source_videos": row.get("source_videos", []),
                    "status": "ACCOUNTED_INCOMPLETE",
                    "synthesis": "NOT_SYNTHESIZED",
                },
                ensure_ascii=False,
            )
            + "\n"
            for row in incomplete
        ),
        encoding="utf-8",
    )
    report = {
        "schema_version": 1,
        "pipeline": "QINXIONGMAO_RESEARCH_PIPELINE",
        "generated_at": datetime.now(UTC).isoformat(),
        "video_root": str(video_root),
        "video_commit": _git_rev(video_root.parents[3]),
        "ai_commit": _git_rev(ai_repo_root),
        "raw_corpus_hash": _sha256(video_root / "catalog.jsonl") if (video_root / "catalog.jsonl").exists() else None,
        "strategy_spec_hashes": {
            path.name: _sha256(path) for path in sorted((research / "strategy_specs").glob("*.json"))
        },
        "video_rule_candidates": len(candidates),
        "video_incomplete_rules": len(incomplete),
        "fragment_accounting": {**_fragment_accounting(incomplete), "artifact": str(accounting_path)},
        "strategy_audits": audits,
        "replay_results": replay_results,
        "validation_policy": {
            "sharpe_min": default_policy.min_sharpe,
            "profit_factor_min": default_policy.min_profit_factor,
            "max_drawdown_max": default_policy.max_drawdown,
            "expectancy_min": default_policy.min_expectancy,
        },
        "promotion_authorized": False,
        "status": "RESEARCH_COMPLETE_NO_PROMOTABLE_STRATEGY" if not replay_results else "RESEARCH_COMPLETE",
        "reason": "当前 Strategy Spec 为长段自然语言且缺少结构化 entry/exit/stop/timeframe/regime/sizing；未满足 Mechanizability Gate，未运行或伪造回测。"
        if not replay_results
        else "仅对通过 Source + Mechanizability Gate 的规则运行现有 TechnicalStrategyValidationService。",
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--video-root", type=Path, required=True)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--ai-repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run(args.video_root, args.database_url, args.days, args.ai_repo_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "status": report["status"],
                "audited": len(report["strategy_audits"]),
                "replayed": len(report["replay_results"]),
                "output": str(args.output),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
