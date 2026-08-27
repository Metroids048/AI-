"""Deterministic export from the video Agent Corpus to quant research JSONL."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from services.research.integrations.contracts import stable_hash

from .contracts import QuantKnowledgeBundle, QuantPrimitive, ResearchHypothesis
from .registry import HypothesisRegistry, QuantPrimitiveRegistry


def _experiment_type(role: str) -> str:
    if role == "EVENT":
        return "ATOMIC_EDGE"
    if role == "EXIT_HYPOTHESIS":
        return "EXIT_ABLATION"
    if role in {"FILTER", "REGIME", "CONFIRMATION", "VETO", "LEVEL"}:
        return "INCREMENTAL_FILTER"
    return "IMPLEMENTATION_REVIEW"


_PARAMETER_PRIORS: dict[str, dict[str, Any]] = {
    "QP_SUPPORT_TOUCH_COUNT": {"touch_count_min": {"values": [2], "origin": "RESEARCH_PARAMETER"}},
    "QP_LEVEL_REACTION_STRENGTH_ATR": {"reaction_strength_atr_min": {"values": [0.5], "origin": "RESEARCH_PARAMETER"}},
    "QP_LEVEL_AGE": {"level_age_min": {"values": [4], "origin": "RESEARCH_PARAMETER"}},
    "QP_BREAKOUT_DISTANCE_ATR": {"breakout_distance_atr_min": {"values": [0.5], "origin": "RESEARCH_PARAMETER"}},
    "QP_TREND_PULLBACK_DEPTH": {"pullback_depth_atr_min": {"values": [0.0], "origin": "RESEARCH_PARAMETER"}},
    "QP_VOLUME_CONFIRMATION": {"volume_ratio_min": {"values": [1.5], "origin": "RESEARCH_PARAMETER"}},
    "QP_WICK_REJECTION": {"wick_body_ratio_min": {"values": [1.5], "origin": "RESEARCH_PARAMETER"}},
    "QP_MULTI_TIMEFRAME_AGREEMENT": {"trend_strength_1h_min": {"values": [0.1], "origin": "RESEARCH_PARAMETER"}},
    "QP_NO_TRADE_CHOP_VETO": {"chop_score_max": {"values": [0.6], "origin": "RESEARCH_PARAMETER"}},
}

_TEMPLATES: tuple[dict[str, Any], ...] = (
    {
        "id": "QP_SUPPORT_TOUCH_COUNT",
        "concept": "支撑压力触及次数",
        "role": "LEVEL",
        "terms": ("支撑", "压力", "关键位", "碰", "触及"),
        "features": ("touch_count", "distance_to_level_atr"),
        "quantization": {
            "level_definition": "point_in_time swing level",
            "touch_condition": "distance <= tolerance * ATR",
        },
        "thesis": "被多次触及的价位可能承载更强的市场记忆。",
        "risk": "MEDIUM",
    },
    {
        "id": "QP_LEVEL_REACTION_STRENGTH_ATR",
        "concept": "关键位反应强度",
        "role": "FILTER",
        "terms": ("支撑", "压力", "反应", "反弹", "回落"),
        "features": ("reaction_strength_atr", "distance_to_level_atr"),
        "quantization": {"reaction_strength_atr": "max adverse-to-favorable excursion from level / ATR"},
        "thesis": "从关键位离开时的 ATR 归一化反应越强，突破或回踩的后续质量可能越高。",
        "risk": "MEDIUM",
    },
    {
        "id": "QP_LEVEL_AGE",
        "concept": "关键位存续时间",
        "role": "LEVEL",
        "terms": ("支撑", "压力", "关键位", "时间", "持续", "形成"),
        "features": ("level_age", "distance_to_level_atr", "swing_level"),
        "quantization": {"level_age": "bars since point-in-time level was last refreshed"},
        "thesis": "关键位形成后的存续时间可能影响其后续反应，但必须以点时定义验证。",
        "risk": "HIGH",
    },
    {
        "id": "QP_SUPPORT_ROLE_REVERSAL",
        "concept": "支撑压力角色互换",
        "role": "FILTER",
        "terms": ("支撑", "压力", "突破", "回踩", "站稳", "角色"),
        "features": ("swing_level", "distance_to_level_atr", "retest_depth_atr", "close_back_above_level"),
        "quantization": {
            "level_definition": "prior point-in-time swing level",
            "break_condition": "close beyond level by break_distance_atr",
            "retest_condition": "price returns within retest_depth_atr",
            "hold_condition": "close remains on the broken side for confirmation bars",
        },
        "thesis": "原压力突破后回踩并维持在其上方时，该区域可能转变为支撑，反之亦然。",
        "risk": "MEDIUM",
    },
    {
        "id": "QP_BREAKOUT_DISTANCE_ATR",
        "concept": "突破距离",
        "role": "EVENT",
        "terms": ("突破", "有效突破", "破位", "站上", "跌破"),
        "features": ("breakout_distance_atr", "swing_level"),
        "quantization": {"break_condition": "close displacement beyond level / ATR"},
        "thesis": "突破离开关键位的距离可作为事件强度的可复现 proxy。",
        "risk": "LOW",
    },
    {
        "id": "QP_BREAKOUT_RETEST",
        "concept": "突破回踩",
        "role": "EVENT",
        "terms": ("突破", "回踩", "回测", "假突破"),
        "features": ("swing_level", "breakout_distance_atr", "retest_depth_atr", "close_back_above_level"),
        "quantization": {
            "break_condition": "prior close beyond level",
            "retest_condition": "next bars revisit level",
            "hold_condition": "close confirms direction",
        },
        "thesis": "突破后的回踩与重新确认构成可独立统计的事件，而不是完整策略。",
        "risk": "MEDIUM",
    },
    {
        "id": "QP_MARKET_STRUCTURE_HH_HL",
        "concept": "高高低低市场结构",
        "role": "REGIME",
        "terms": ("道氏", "高点", "低点", "高高", "低低", "结构"),
        "features": ("pivot_sequence", "higher_high", "higher_low", "trend_age"),
        "quantization": {
            "pivot_definition": "confirmed pivot with fixed left/right bars",
            "regime_condition": "HH/HL sequence known at bar close",
        },
        "thesis": "点时确认的 HH/HL 或 LH/LL 序列可作为趋势状态与交易过滤器。",
        "risk": "HIGH",
    },
    {
        "id": "QP_TREND_EMA_ALIGNMENT",
        "concept": "均线排列与斜率",
        "role": "REGIME",
        "terms": ("均线", "排列", "多头", "空头", "趋势"),
        "features": ("ema_alignment", "ema_slope", "trend_age", "trend_strength"),
        "quantization": {
            "alignment": "EMA_fast > EMA_mid > EMA_slow or inverse",
            "slope": "point-in-time EMA delta / ATR",
        },
        "thesis": "均线排列与斜率可能区分趋势背景，但不单独构成入场策略。",
        "risk": "LOW",
    },
    {
        "id": "QP_TREND_PULLBACK_DEPTH",
        "concept": "顺势回调深度",
        "role": "FILTER",
        "terms": ("顺势回调", "回调", "回撤", "趋势"),
        "features": ("pullback_depth_atr", "trend_age", "trend_strength"),
        "quantization": {
            "pullback_depth_atr": "distance from impulse extreme / ATR",
            "trend_condition": "same-side structure remains valid",
        },
        "thesis": "趋势中的回调深度可能改变后续事件的条件期望。",
        "risk": "MEDIUM",
    },
    {
        "id": "QP_VOLUME_CONFIRMATION",
        "concept": "成交量确认",
        "role": "CONFIRMATION",
        "terms": ("成交量", "放量", "量能", "量价"),
        "features": ("volume_ratio", "volume_percentile"),
        "quantization": {"volume_ratio": "event volume / rolling median volume"},
        "thesis": "相对成交量确认可能改善突破或回踩事件的筛选质量。",
        "risk": "LOW",
    },
    {
        "id": "QP_WICK_REJECTION",
        "concept": "影线拒绝",
        "role": "CONFIRMATION",
        "terms": ("Pin Bar", "针", "影线", "拒绝", "吞没", "K线"),
        "features": ("wick_body_ratio", "close_location", "rejection_flag"),
        "quantization": {"rejection_flag": "wick/body ratio and close location thresholds"},
        "thesis": "K 线拒绝形态更适合作为已有事件的确认条件，而非 standalone signal。",
        "risk": "MEDIUM",
    },
    {
        "id": "QP_MULTI_TIMEFRAME_AGREEMENT",
        "concept": "多周期一致性",
        "role": "FILTER",
        "terms": ("多周期", "周期", "级别", "小时", "日线"),
        "features": ("regime_4h", "trend_strength_1h", "timeframe_agreement"),
        "quantization": {"agreement": "higher timeframe direction matches event timeframe"},
        "thesis": "高低周期方向一致可能改善事件的条件期望。",
        "risk": "HIGH",
    },
    {
        "id": "QP_NO_TRADE_CHOP_VETO",
        "concept": "震荡环境不交易",
        "role": "VETO",
        "terms": ("震荡", "不要交易", "不交易", "观望", "不要做"),
        "features": ("chop_score", "atr_percentile", "trend_strength"),
        "quantization": {"veto_condition": "chop_score above registered threshold or trend strength below floor"},
        "thesis": "在缺少方向或波动结构不适合时拒绝事件，作为 veto 研究而非预测。",
        "risk": "MEDIUM",
    },
    {
        "id": "QP_STOP_STRUCTURE_PRIOR",
        "concept": "结构止损位置先验",
        "role": "PARAMETER_PRIOR",
        "terms": ("止损", "止盈", "风险", "仓位"),
        "features": ("stop_distance_atr", "swing_level", "mfe", "mae"),
        "quantization": {"stop_reference": "structural invalidation level, measured point-in-time"},
        "thesis": "止损应锚定可回测的失效结构；该条目仅提供参数先验，不改变执行硬限制。",
        "risk": "HIGH",
    },
)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _source_manifest(corpus_root: Path, units: list[dict[str, Any]], rules: list[dict[str, Any]]) -> dict[str, Any]:
    files = [
        corpus_root / "agent_corpus" / "knowledge_units.jsonl",
        corpus_root / "strategy_research" / "rule_candidates.jsonl",
    ]
    file_hashes: dict[str, str] = {}
    labels = ("agent_corpus/knowledge_units.jsonl", "strategy_research/rule_candidates.jsonl")
    for path, label in zip(files, labels, strict=True):
        if path.exists():
            file_hashes[label] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "source_type": "YOUTUBE_AGENT_CORPUS",
        "corpus_root": str(corpus_root),
        "agent_corpus_path": str(corpus_root / "agent_corpus"),
        "rule_candidates_path": str(corpus_root / "strategy_research" / "rule_candidates.jsonl"),
        "knowledge_unit_count": len(units),
        "rule_candidate_count": len(rules),
        "file_hashes": file_hashes,
        "source_hash": stable_hash(file_hashes),
        "generated_at": datetime.now(UTC).isoformat(),
    }


def export_quant_knowledge(
    corpus_root: str | Path,
    output_dir: str | Path,
    *,
    min_support_for_hypothesis: int = 1,
) -> QuantKnowledgeBundle:
    """Build the bounded first-pass export; no strategy specs or execution writes."""
    root = Path(corpus_root)
    out = Path(output_dir)
    units = _read_jsonl(root / "agent_corpus" / "knowledge_units.jsonl")
    rules = _read_jsonl(root / "strategy_research" / "rule_candidates.jsonl")
    manifest = _source_manifest(root, units, rules)
    registry = QuantPrimitiveRegistry()
    proposals: list[dict[str, Any]] = []
    hypotheses: list[ResearchHypothesis] = []
    for template in _TEMPLATES:
        matches = [
            unit
            for unit in units
            if any(term.lower() in str(unit.get("content", "")).lower() for term in template["terms"])
            or any(term in " ".join(map(str, unit.get("topic", []))) for term in template["terms"])
        ]
        source_videos = sorted({str(unit.get("video_id")) for unit in matches if unit.get("video_id")})
        source_units = sorted({str(unit.get("unit_id")) for unit in matches if unit.get("unit_id")})
        source_refs = sorted({str(ref) for unit in matches for ref in unit.get("clean_source_refs", [])})
        primitive = QuantPrimitive(
            primitive_id=template["id"],
            concept=template["concept"],
            role=template["role"],
            source_support_count=len(source_units),
            source_videos=source_videos,
            source_units=source_units,
            natural_language_thesis=template["thesis"],
            quantization_status="PROXY_ALLOWED",
            quantization=template["quantization"],
            required_features=list(template["features"]),
            parameter_priors=_PARAMETER_PRIORS.get(template["id"], {}),
            lookahead_risk=template["risk"],
            contradictions=[],
            provenance="PROXY_DERIVED",
            applicable_timeframes=sorted({str(x) for unit in matches for x in unit.get("timeframe", [])}),
            applicable_regimes=sorted({str(x) for unit in matches for x in unit.get("market", [])}),
            source_refs=source_refs,
        )
        registry.register(primitive)
        for unit in matches:
            proposals.append(
                {
                    "proposal_id": f"QPROP-{primitive.primitive_id}-{unit.get('unit_id')}",
                    "primitive_id": primitive.primitive_id,
                    "source_unit": unit.get("unit_id"),
                    "source_video": unit.get("video_id"),
                    "source_refs": unit.get("clean_source_refs", []),
                    "quantization_status": primitive.quantization_status,
                    "provenance": primitive.provenance,
                    "proxy_definition": primitive.quantization,
                    "research_only": True,
                }
            )
        if len(source_units) >= min_support_for_hypothesis and template["role"] in {
            "EVENT",
            "FILTER",
            "REGIME",
            "CONFIRMATION",
            "VETO",
            "LEVEL",
        }:
            base_event = "HTF_BREAK_RETEST" if template["role"] != "EVENT" else primitive.primitive_id
            hypothesis = ResearchHypothesis(
                hypothesis_id=f"HYP-QK-V2-{primitive.primitive_id}",
                claim=f"在 {base_event} 事件中，加入 {primitive.concept} 后 OOS net expectancy 改善。",
                base_event=base_event,
                primitive=primitive.primitive_id,
                research_design_version=2,
                experiment_type=_experiment_type(primitive.role),
                parent_universe={"selector": "canonical_eventedge_development", "base_event": base_event},
                baseline_selector={"event_type": base_event},
                candidate_selector={"primitive_id": primitive.primitive_id, "point_in_time": True},
                parameter_space={
                    "source": "primitive_parameter_priors",
                    "values": primitive.parameter_priors,
                },
                feature_formula_hash=stable_hash(
                    {
                        "primitive_id": primitive.primitive_id,
                        "quantization": primitive.quantization,
                        "required_features": primitive.required_features,
                    }
                ),
                metric="net_expectancy",
                horizons=[4, 8, 16],
                split_plan={"method": "walk_forward", "paired_comparison": True},
                cost_model={"required": True},
                symbols=["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "BNB/USDT"],
                timeframes=["15m", "1h", "4h"],
                source_refs=source_refs,
                registered_before_evaluation=True,
            )
            hypotheses.append(hypothesis)
    hypothesis_registry = HypothesisRegistry(hypotheses)
    hypotheses = hypothesis_registry.list()
    bundle = QuantKnowledgeBundle(
        corpus_id="qinxiongmao",
        generated_at=datetime.now(UTC).isoformat(),
        source_manifest=manifest,
        primitives=registry.list(),
        hypotheses=hypotheses,
        quantization_proposals=proposals,
    )
    out.mkdir(parents=True, exist_ok=True)
    records = [{"record_type": "primitive", **item.model_dump(mode="json")} for item in bundle.primitives]
    records.extend({"record_type": "hypothesis", **item.model_dump(mode="json")} for item in bundle.hypotheses)
    _write_jsonl(out / "quant_knowledge_bundle.jsonl", records)
    _write_jsonl(
        out / "quant_concepts.jsonl",
        [
            {
                "primitive_id": item.primitive_id,
                "concept": item.concept,
                "role": item.role,
                "source_support_count": item.source_support_count,
                "provenance": item.provenance,
            }
            for item in bundle.primitives
        ],
    )
    _write_jsonl(out / "quantization_proposals.jsonl", bundle.quantization_proposals)
    export_manifest = {
        **manifest,
        "export_hash": bundle.export_hash,
        "primitive_count": len(bundle.primitives),
        "hypothesis_count": len(bundle.hypotheses),
        "proposal_count": len(bundle.quantization_proposals),
    }
    (out / "source_manifest.json").write_text(
        json.dumps(export_manifest, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8"
    )
    return bundle


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) + "\n" for row in rows),
        encoding="utf-8",
    )


__all__ = ["export_quant_knowledge"]
