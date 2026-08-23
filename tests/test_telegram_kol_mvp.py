from decimal import Decimal

from services.agents.telegram_kol.ocr import parse_ocr_blocks
from services.agents.telegram_kol.parser import parse_message
from services.agents.telegram_kol.thread import TradeThreadBook


def test_complete_text_signal_is_structured():
    event = parse_message("陈哥", "BTC，77000-77300附近，做多\n止损预计：75000\n止盈预计：79000/81500")
    assert event.event_type == "OPEN"
    assert event.symbol == "BTC/USDT"
    assert event.side == "LONG"
    assert event.entry_type == "RANGE"
    assert event.entry_low == Decimal("77000")
    assert event.entry_high == Decimal("77300")
    assert event.stop_loss == Decimal("75000")
    assert [tp.price for tp in event.take_profits] == [Decimal("79000"), Decimal("81500")]
    assert event.execution_readiness == "COMPLETE"


def test_incomplete_market_signal_fails_closed():
    event = parse_message("sample", "BTC市价直接做多")
    assert event.event_type == "OPEN"
    assert event.execution_readiness == "INCOMPLETE"
    assert "stop_loss" in event.missing_fields


def test_commentary_does_not_become_open():
    event = parse_message(
        "米哥",
        "比特币7.3万附近大阻力，有开多单的可以考虑止盈一部分，回踩差不多6.9-7万附近可以重新接回。",
    )
    assert event.event_type == "COMMENTARY"
    assert event.execution_readiness == "NOT_EXECUTABLE"


def test_follow_up_management_keeps_thread_direction():
    book = TradeThreadBook()
    opened = parse_message("飞扬", "ZEC做多 820附近 止损795 止盈860")
    thread = book.apply(opened)
    update = parse_message(
        "飞扬",
        "ZEC现价852，获利32点，空单止盈一半，剩余仓位止损放在800。止盈取消",
        context_side=thread.side,
    )
    thread = book.apply(update)
    assert thread.side == "LONG"
    assert thread.state == "PARTIALLY_CLOSED"
    assert thread.stop_loss == "800"
    assert thread.take_profits == []
    assert "DIRECTION_CONTEXT_CONFLICT" in update.warnings


def test_ocr_text_with_multiple_bubbles_is_split_into_events():
    ocr = """飞扬合约策略
具体产品 : ZEC
进行方向 : 做多
进场点位 : 820附近
止损点位 : 795
止盈点位 : 860
ZEC现价852，获利32点，空单止盈一半，剩余
仓位止损放在800。止盈取消
锁定4连胜，今天没开比特币，比特币的思路看视频
"""
    events = parse_ocr_blocks("飞扬", ocr)
    assert [event.event_type for event in events] == ["OPEN", "POSITION_UPDATE", "COMMENTARY"]
    assert events[0].symbol == "ZEC/USDT"
    assert events[1].side == "LONG"


def test_add_position_without_size_is_incomplete():
    event = parse_message("军长", "TRB现价加一次仓")
    assert event.event_type == "ADD_POSITION"
    assert event.execution_readiness == "INCOMPLETE"
    assert event.missing_fields == ["add_position_size"]


def test_complex_open_message_captures_planned_add_and_targets():
    event = parse_message(
        "军长",
        "BTC 长线做多\n77100附近市价直接做多 100倍 2%保证金\n再挂73888补仓 100倍 3%保证金\n"
        "第一止盈79388 止盈70%仓位利润\n第二止盈81000 全部止盈\n止损72000",
    )
    assert event.event_type == "OPEN"
    assert event.entry_price == Decimal("77100")
    assert event.claimed_leverage == 100
    assert event.claimed_position_fraction == Decimal("0.02")
    assert [(tp.price, tp.close_fraction) for tp in event.take_profits] == [
        (Decimal("79388"), Decimal("0.70")),
        (Decimal("81000"), Decimal("1")),
    ]
    assert any(action.action_type == "PLANNED_ADD" and action.price == Decimal("73888") for action in event.actions)


def test_ocr_noise_is_not_treated_as_symbol():
    event = parse_message("陈哥", "PODER EY NO40UUSSTT")
    assert event.symbol is None
    assert event.event_type == "COMMENTARY"
