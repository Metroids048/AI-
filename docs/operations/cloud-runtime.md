# Linux Cloud Runtime

This deployment runs the existing V2 API and `RuntimeScheduler` as two systemd
processes. The cloud VM is the only `v2_active` writer. While it is active, do
not explicitly start the local Windows launcher in `v2_active`; use local
shadow mode for development and observation.

## Layout

- Application checkout: `/opt/ai-quant`
- Virtual environment: `/opt/ai-quant/.venv`
- Private environment file: `/etc/ai-quant/cloud.env` (never commit it)
- Persistent database and runtime state: `/var/lib/ai-quant/runtime`
- Nginx static frontend root: `/opt/ai-quant/frontend/admin/dist`

Install dependencies and build the frontend before enabling services. Prepare
the SQLite schema once after setting the private environment file:

```bash
cd /opt/ai-quant
source .venv/bin/activate
python scripts/prepare_database.py --database-url "$POSTGRES_URL"
npm --workspace frontend/admin run build
sudo install -m 0644 deploy/systemd/ai-quant-api.service /etc/systemd/system/
sudo install -m 0644 deploy/systemd/ai-quant-scheduler.service /etc/systemd/system/
sudo install -m 0644 deploy/nginx/ai-quant.conf /etc/nginx/sites-available/ai-quant
sudo ln -s /etc/nginx/sites-available/ai-quant /etc/nginx/sites-enabled/ai-quant
sudo systemctl daemon-reload
sudo systemctl enable --now ai-quant-api.service ai-quant-scheduler.service nginx
```

`/etc/ai-quant/cloud.env` must set the persistent SQLite/state paths plus the
existing Testnet credentials. Its required execution values are:

```dotenv
APP_ENV=testnet
ADMIN_API_TOKEN=<private-non-default-token>
POSTGRES_URL=sqlite:////var/lib/ai-quant/runtime/ai-quant.db
LOCAL_SCHEDULER_STATE_PATH=/var/lib/ai-quant/runtime/scheduler-state.json
SCHEDULER_PID_PATH=/var/lib/ai-quant/runtime/scheduler.pid
AUTOMATED_TRADING_ENGINE=v2_active
BINANCE_USE_TESTNET=true
LIVE_TRADING_ENABLED=false
BINANCE_AUTO_EXECUTE=true
BINANCE_API_KEY=<private>
BINANCE_API_SECRET=<private>
V2_ALLOW_UNMANAGED_EXTERNAL_POSITIONS=true
V2_EXTERNAL_BASELINE_PATH=/var/lib/ai-quant/runtime/testnet-external-baseline.json
V2_EXTERNAL_BASELINE_SOURCE=persistent_file:/var/lib/ai-quant/runtime/testnet-external-baseline.json
V2_EXTERNAL_BASELINE_JSON=<positions-json-from-the-persisted-baseline>
```

Capture the external Testnet baseline into the persistent path using the
existing capture workflow before starting the scheduler. The deployment
preflight rejects a missing, mismatched, non-Testnet, or non-active contract.
It does not contact Binance or claim resource acceptance.

Only the two services above are enabled. Do not provision Celery beat or a
Celery worker for this V2 runtime. systemd owns one scheduler process; the
existing SQLite-backed `SchedulerCoordinator` and fencing remain in place for
the shared database.

P0 code readiness is not external cloud acceptance. On the actual VM, verify
service restart and reboot recovery, `/api/v1/health`, fresh scheduler state,
three closed 15-minute candles, ongoing BTC/ETH decisions, reconciliation, and
the measured API/Scheduler/Nginx RSS. Treat steady total usage above 700 MB or
short peaks above 850 MB as `CLOUD_RESOURCE_GATE_FAILED`; move to an Oracle
Always-Free ARM VM rather than changing trading code.
