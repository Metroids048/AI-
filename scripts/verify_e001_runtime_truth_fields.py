"""Read-only verification of the E-001 Runtime Truth account contract.

Queries the running local API and asserts the snapshot exposes the five required
numeric account fields, and that an unavailable exchange never degrades to a
numeric zero placeholder. Prints current open-position notionals so the E-010
sizing change can be compared before/after.

Read-only: performs GETs against 127.0.0.1 only. Submits nothing.
"""

from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path

API_BASE = "http://127.0.0.1:8016"
REQUIRED_ACCOUNT_FIELDS = (
    "wallet_balance",
    "available_balance",
    "margin_balance",
    "unrealized_pnl",
    "open_position_count",
)


def _token() -> str:
    """Read the admin token from the frontend env, falling back to the dev default."""
    env_path = Path("frontend/admin/.env")
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.startswith("VITE_ADMIN_API_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"')
    return os.environ.get("ADMIN_API_TOKEN", "dev-admin-token")


def _get(path: str, token: str) -> dict:
    request = urllib.request.Request(
        f"{API_BASE}{path}",
        headers={"Authorization": f"Bearer {token}"},
    )
    # Bypass any configured proxy for loopback.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    token = _token()
    failures: list[str] = []

    snapshot = _get("/api/v1/runtime/snapshot", token)
    exchange = snapshot.get("exchange") or {}
    status = exchange.get("status")

    print(f"exchange.status : {status}")
    print(f"observed_at     : {exchange.get('observed_at')}")

    account = (exchange.get("value") or {}).get("account") or {}
    print("\nE-001 required numeric fields:")
    for field in REQUIRED_ACCOUNT_FIELDS:
        present = field in account
        value = account.get(field)
        print(f"  {field:22}: {value!r} ({type(value).__name__}) present={present}")
        if status == "available" and not present:
            failures.append(f"{field} missing while exchange is available")

    # T-001: unavailable must not be represented as a numeric zero.
    if status in {"unavailable", "stale"}:
        zeroed = [f for f in REQUIRED_ACCOUNT_FIELDS if account.get(f) == 0]
        if zeroed:
            failures.append(f"status={status} but fields zero-filled: {zeroed}")
        print(f"\nT-001 check: status={status}, zero-filled fields={zeroed or 'none'}")
    else:
        print("\nT-001 check: exchange available; zero-placeholder path not exercised")

    # Field names come from the runtime positions payload: `contracts` and `side`
    # (ccxt unified), not `quantity`/`direction`.
    positions = (exchange.get("value") or {}).get("positions") or []
    equity = float(account.get("margin_balance") or account.get("wallet_balance") or 0)
    print(f"\nopen positions  : {len(positions)}")
    total_notional = 0.0
    for position in positions:
        contracts = abs(float(position.get("contracts") or 0))
        mark = float(position.get("mark_price") or 0)
        leverage = float(position.get("leverage") or 0)
        notional = contracts * mark
        total_notional += notional
        fraction = (notional / equity) if equity > 0 else 0.0
        margin = (notional / leverage) if leverage > 0 else 0.0
        print(
            f"   {position.get('symbol')} {position.get('side')} "
            f"contracts={contracts} mark={mark} notional={notional:.2f} "
            f"({fraction:.2%} of equity) leverage={leverage:.0f}x margin={margin:.2f}"
        )
    if positions:
        print(f"   total notional : {total_notional:.2f}")

    reconciliation = _get("/api/v1/runtime/reconciliation", token)
    print(f"\nreconciliation  : {reconciliation.get('status')}")
    print(f"blocked symbols : {reconciliation.get('entry_blocked_symbols')}")

    if failures:
        print("\nFAILURES:")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print("\nE-001 runtime truth contract: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
