# Supported Scripts

Supported operator entry points are intentionally small:

- Startup and database: `launch-paper-console.ps1`, `run-local-paper-scheduler.py`, `prepare_database.py`.
- Runtime verification: `verify_config.py`, `verify_runtime_config_sync.py`, `audit_decision_funnel.py`, `audit_symbol_data_completeness.py`.
- Evidence and validation: `compute_signal_edge_stats.py`, `run_top20_technical_validation.py`, `compare_exit_policies_cli.py`, `train_meta_label_model.py`.
- Portable runtime ledger (ADR-073): `export_runtime_ledger.py`, `import_runtime_ledger.py`.
- Data and lifecycle: `data_sync.py`, `data_check.py`, `audit_full_lifecycle_completion.py`.
- Binance simulation acceptance: `testnet_preflight.py`, `run_testnet_acceptance.py`, `smoke_binance_simulation_path.py`.
- Engineering checks: `compose_validate.py`, `compose_smoke.py`, `verify_quant_dependencies.py`, `clean_test_artifacts.py`.

Files under `scripts/archive/` are unsupported historical diagnostics and must not be used for current decisions.
