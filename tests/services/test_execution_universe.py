from services.data.universe import AUTO_SIMULATION_EXECUTION_SYMBOLS, execution_scope_hash


def test_execution_scope_is_operator_approved_top_five() -> None:
    assert AUTO_SIMULATION_EXECUTION_SYMBOLS == (
        "BTC/USDT",
        "ETH/USDT",
        "SOL/USDT",
        "XRP/USDT",
        "BNB/USDT",
    )
    assert execution_scope_hash() == "9d4e56ae53f9d0b1047efebc5ac48b28fa0cfa72d71b28178dc47dd9b11d124d"
