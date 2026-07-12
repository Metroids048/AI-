"""Build the operator playbook from code defaults and structured research manifests."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

from sqlalchemy.orm import Session

from services.execution.bootstrap import AUTO_PAPER_TECHNICAL_RULES, OPERATOR_EXPERIENCE_RULES
from services.strategy_library.models import StrategyRoadmapState
from shared.models import (
    DecisionStage,
    ExitRule,
    ExternalStrategySource,
    LlmRagBoundary,
    OptimizationRoadmapItem,
    PlaybookMetadata,
    PositionSizingPolicy,
    RiskProfile,
    RoadmapAuditEntry,
    RoadmapUpdate,
    ScopedDefault,
    StrategyChannel,
    StrategyPlaybook,
    TechnicalSignalDefinition,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_MANIFEST = REPO_ROOT / "research_source/open_source_strategy_library/manifests/seed_sources.json"

ROADMAP_DEFINITIONS = (
    ("chan-objective-algorithm", "缠论客观算法", "P1", "定义无歧义的分型、笔和买卖点规则后再进入回测。", "技术信号"),
    (
        "timeframe-signal-specialization",
        "4h/15m 信号子集分工",
        "P0",
        "4h侧重结构趋势，15m侧重假突破与反转入场。",
        "多周期确认",
    ),
    ("meta-label-oos-validation", "MetaLabel样本外验证", "P0", "用更长窗口和样本外评估替代短样本近似。", "MetaLabel"),
    ("portfolio-correlation-risk", "组合相关性与净敞口", "P0", "补齐单品种、相关性簇和组合净敞口限制。", "Risk Engine"),
    ("review-agent-llm", "Review Agent真实LLM接入", "P1", "在规则聚合后增加受约束的归因与复盘建议。", "Review Layer"),
    ("rag-vector-retrieval", "RAG向量化评估", "P1", "资产规模增长后评估语义检索和可追溯引用。", "AI Agent Layer"),
    (
        "mean-reversion-regime-filter",
        "均值回归Regime过滤",
        "P1",
        "用趋势/波动状态约束RSI和布林带回归。",
        "Strategy Layer",
    ),
    (
        "liquidity-oi-position-cap",
        "订单簿与OI仓位约束",
        "P2",
        "把深度、冲击成本和OI纳入入场与仓位上限。",
        "Execution Layer",
    ),
)


def build_playbook(db: Session) -> StrategyPlaybook:
    states = {row.item_id: row for row in db.query(StrategyRoadmapState).all()}
    return StrategyPlaybook(
        metadata=PlaybookMetadata(
            verified_on="2026-07-12",
            verified_commit="80ce3d62f7854aad848a430bb391963ac89f8d99",
            source_documents=[
                "02_量化策略与LLM+RAG开平单逻辑详细报告.md",
                "策略库/00_当前系统策略与开平单逻辑.md",
                "策略库/01_外部策略来源索引.md",
            ],
            disclaimer="这是固定核对截面的代码行为说明，不是实时运行状态镜像。",
        ),
        channels=[
            StrategyChannel(
                channel_id="funding_carry",
                name="资金费率套利",
                positioning="市场中性双腿套利",
                core_assumption="正资金费率净收益覆盖四腿手续费、滑点和基差风险",
                maturity="最成熟",
            ),
            StrategyChannel(
                channel_id="technical_directional",
                name="技术方向性",
                positioning="多周期方向交易",
                core_assumption="低相关技术信号、多周期和风险关口共同提高入场质量",
                maturity="持续迭代",
            ),
        ],
        decision_stages=_decision_stages(),
        technical_signals=_technical_signals(),
        exit_rules=_exit_rules(),
        position_sizing=_position_sizing(),
        llm_rag=_llm_rag_boundary(),
        external_sources=_external_sources(),
        roadmap=[_roadmap_item(definition, states.get(definition[0])) for definition in ROADMAP_DEFINITIONS],
    )


def update_roadmap_item(db: Session, item_id: str, update: RoadmapUpdate) -> OptimizationRoadmapItem | None:
    definition = next((item for item in ROADMAP_DEFINITIONS if item[0] == item_id), None)
    if definition is None:
        return None
    row = db.get(StrategyRoadmapState, item_id)
    if row is None:
        row = StrategyRoadmapState(item_id=item_id, status="pending", audit_history=[])
        db.add(row)
    status = update.status or row.status
    note = update.note if update.note is not None else row.note
    updated_by = (update.updated_by or "operator").strip() or "operator"
    changed_at = datetime.now(UTC)
    history = list(row.audit_history or [])
    history.append(
        {
            "status": status,
            "note": note,
            "updated_by": updated_by,
            "updated_at": changed_at.isoformat(),
        }
    )
    row.status = status
    row.note = note
    row.updated_by = updated_by
    row.updated_at = changed_at
    row.audit_history = history
    db.commit()
    db.refresh(row)
    return _roadmap_item(definition, row)


def _decision_stages() -> list[DecisionStage]:
    stages = (
        ("closed_bar", "新K线", "只使用已收线OHLCV，拒绝未来数据。"),
        ("technical_signals", "8个技术信号", "独立生成方向、置信度和触发原因。"),
        ("multi_timeframe", "多周期确认", "方向周期必须与入场周期一致，否则失败关闭。"),
        ("signal_ensemble", "SignalEnsemble", "相关性过高时将较弱信号权重乘0.25后加权投票。"),
        ("meta_label", "MetaLabel", "近期已收线样本计算胜率与均值收益，决定是否下注。"),
        ("llm_veto", "LLM Veto", "只能二元否决，不能改变方向、价格或仓位。"),
        ("gatekeeper", "Gatekeeper", "校验止损、验证证据、数据新鲜度和风险上限。"),
        ("exchange_order", "下单", "通过Gateway提交并以交易所确认结果更新账本。"),
    )
    return [DecisionStage(stage_id=item[0], name=item[1], description=item[2]) for item in stages]


def _technical_signals() -> list[TechnicalSignalDefinition]:
    values = (
        (
            "macd",
            "MACD",
            {"fast": 12, "slow": 26, "signal": 9, "cross_lookback": 7},
            "近期金叉/死叉，否则使用histogram动量",
            "趋势与动量",
        ),
        ("dow_trend", "道氏结构", {"pivot_window": 3}, "更高高点和更高低点确认多头，反向确认空头", "结构方向"),
        ("price_action", "价格行为", {"donchian_lookback": 20}, "吞没、pin bar、突破及破位收回的假突破", "入场触发"),
        ("rsi", "RSI", {"period": 14, "oversold": 30, "overbought": 70}, "从超卖/超买区域反向穿越阈值", "反转确认"),
        (
            "ema_trend",
            "EMA趋势",
            {"fast": 20, "slow": 50, "min_strength": 0.0015},
            "快慢线价差与慢线斜率同向",
            "趋势确认",
        ),
        ("adx", "ADX", {"period": 14, "min_adx": 22}, "ADX与DI方向差同时满足", "趋势强度"),
        ("vwap", "VWAP", {"lookback": 48}, "价格重新站上或跌破滚动VWAP", "成本锚点"),
        ("bollinger", "布林带", {"period": 20, "stddev": 2}, "价格从带外重新收回带内", "均值回归"),
    )
    return [
        TechnicalSignalDefinition(signal_id=v[0], name=v[1], parameters=v[2], trigger=v[3], role=v[4]) for v in values
    ]


def _exit_rules() -> list[ExitRule]:
    values = (
        ("protective_exit", "保护性止损/止盈", 1, "按K线high/low检查预先记录的保护价。"),
        ("stop_before_profit", "同K线优先止损", 2, "止损和止盈同时触发时采用保守止损结果。"),
        ("trailing_ratchet", "移动止损只收紧", 3, "达到trail_after_r后收紧到入场价，永不放松。"),
        ("opposite_close_only", "反向信号先平仓", 4, "close-only平旧仓，不在同一周期直接反手。"),
    )
    return [ExitRule(rule_id=v[0], name=v[1], priority=v[2], description=v[3]) for v in values]


def _position_sizing() -> PositionSizingPolicy:
    technical = AUTO_PAPER_TECHNICAL_RULES
    operator = OPERATOR_EXPERIENCE_RULES
    return PositionSizingPolicy(
        formula="目标数量 = 账户权益 x risk_per_trade / abs(参考价 - 止损价)，再受品种仓位比例和杠杆上限约束",
        defaults=[
            ScopedDefault(
                key="technical_meta_label_min_win_rate",
                value=technical["entry_rules"]["meta_label_min_win_rate"],
                scope="自动成熟模板1h通道",
                source_ref="services/execution/bootstrap.py:AUTO_PAPER_TECHNICAL_RULES",
            ),
            ScopedDefault(
                key="operator_risk_per_trade",
                value=operator["position_rules"]["risk_per_trade"],
                scope="4h/15m运营者经验研究通道",
                source_ref="services/execution/bootstrap.py:OPERATOR_EXPERIENCE_RULES",
            ),
            ScopedDefault(
                key="operator_max_leverage",
                value=operator["position_rules"]["max_leverage"],
                scope="4h/15m运营者经验研究通道",
                source_ref="services/execution/bootstrap.py:OPERATOR_EXPERIENCE_RULES",
            ),
            ScopedDefault(
                key="generic_risk_profile_max_leverage",
                value=RiskProfile().max_leverage,
                scope="未选择专用风险配置时的通用RiskProfile",
                source_ref="shared/models/risk.py:RiskProfile",
            ),
        ],
        limitations=[
            "交易所实时权益与本地Paper权益仍需持续对账",
            "尚无组合相关性簇和净敞口硬门槛",
            "自动仓位尚未按订单簿深度和冲击成本缩量",
        ],
    )


def _llm_rag_boundary() -> LlmRagBoundary:
    return LlmRagBoundary(
        allowed=["对已规则化候选做二元否决", "解释风险上下文", "支持研究与复盘归因"],
        forbidden=["决定交易方向", "修改入场价或止损", "决定仓位", "绕过Validation或Gatekeeper", "直接向交易所下单"],
        retrieval_mode="keyword_overlap_local_markdown",
        provider_chain=["Anthropic", "OpenRouter free models", "GitHub Models free models"],
        limitations=[
            "当前是关键词重合评分，不是向量检索",
            "Review Agent主要仍是规则聚合",
            "Provider切换会带来模型质量差异",
        ],
    )


def _external_sources() -> list[ExternalStrategySource]:
    raw = json.loads(SOURCE_MANIFEST.read_text(encoding="utf-8"))
    mappings = {
        "crypto_strategy_shapes": "Strategy Layer",
        "crypto_strategy_shapes_candidate": "Strategy Layer",
        "research_framework": "Validation / Strategy Layer",
        "research_framework_candidate": "Validation / Data Layer",
        "llm_research_workflow": "AI Agent / Review Layer",
        "crypto_market_making_shapes": "Strategy / Execution Layer",
    }
    results = []
    for item in raw:
        asset_manifest = (
            REPO_ROOT
            / "research_source/open_source_strategy_library/assets"
            / item["source_id"]
            / "asset_manifest.json"
        )
        results.append(
            ExternalStrategySource(
                source_id=item["source_id"],
                name=item["name"],
                repo_url=item["repo_url"],
                license=item["license"],
                license_policy=item["license_policy"],
                absorbable_content=item["source_notes"],
                platform_mapping=mappings.get(item["project_role"], "Research Source / Strategy Layer"),
                implementation_status="local_assets_ingested" if asset_manifest.exists() else "indexed_only",
            )
        )
    return results


def _roadmap_item(
    definition: tuple[str, str, str, str, str], row: StrategyRoadmapState | None
) -> OptimizationRoadmapItem:
    item_id, title, priority, description, target = definition
    history = [RoadmapAuditEntry.model_validate(item) for item in (row.audit_history if row else [])]
    return OptimizationRoadmapItem(
        item_id=item_id,
        title=title,
        priority=cast(Literal["P0", "P1", "P2"], priority),
        status=cast(Literal["pending", "in_progress", "done"], row.status if row else "pending"),
        description=description,
        optimization_target=target,
        note=row.note if row else None,
        updated_by=row.updated_by if row else None,
        updated_at=row.updated_at if row else None,
        audit_history=history,
    )
