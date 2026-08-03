from services.execution.gateway import configured_gateways
from services.execution.testnet_cleanup import testnet_account_cleanup
from shared.config import settings

print("testnet", settings.binance_use_testnet, "live", settings.live_trading_enabled)
gw = configured_gateways()[0]
# peek first
snap = gw.reconcile(live_run_id="p03-orphan-check")
print("positions", snap.get("open_positions"))
print("open_orders", len(snap.get("open_orders") or []))
for o in snap.get("open_orders") or []:
    print(o)
result = testnet_account_cleanup(gw, idempotency_key="p03-orphan-tp-cleanup")
print("cleanup", result)
snap2 = gw.reconcile(live_run_id="p03-orphan-after")
print("after positions", snap2.get("open_positions"))
print("after orders", len(snap2.get("open_orders") or []))
