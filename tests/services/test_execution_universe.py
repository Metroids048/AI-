from services.data.universe import AUTO_SIMULATION_EXECUTION_SYMBOLS, execution_scope_hash


def test_execution_scope_is_manifest_approved_btc_eth() -> None:
    assert AUTO_SIMULATION_EXECUTION_SYMBOLS == ("BTC/USDT", "ETH/USDT")
    assert execution_scope_hash() == "158a05ba047070491b9442f3e724672d5f9a2d937824d70a901d430c1d4d28f5"
