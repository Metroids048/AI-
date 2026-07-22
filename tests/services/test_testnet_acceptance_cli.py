from __future__ import annotations

from scripts import run_testnet_acceptance


def test_cli_refuses_external_orders_without_explicit_authorization(monkeypatch, capsys) -> None:
    gateway_constructed = False

    def forbidden_gateway(*args, **kwargs):  # noqa: ANN002, ANN003
        nonlocal gateway_constructed
        gateway_constructed = True
        raise AssertionError("gateway must not be constructed")

    monkeypatch.setattr(run_testnet_acceptance, "BinanceUsdtPerpetualGateway", forbidden_gateway)

    exit_code = run_testnet_acceptance.main([])

    assert exit_code == 2
    assert gateway_constructed is False
    assert "external_order_authorization_required" in capsys.readouterr().out
