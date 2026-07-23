# Test Baseline

运行器解析命令（exit 0）：`& "$env:AGENT_PYTHON" "$env:USERPROFILE/.ai-workspace/scripts/resolve-test-runner.py"`。输出选择 `py -3 -m pytest`（`choice=py_launcher`）。`pre-commit install` 已执行，`.git/hooks/pre-commit` 存在。

| 命令 | 退出码 | 结果 | 备注 |
|---|---:|---|---|
| `py -3 -m ruff check .` | 0 | PASS | 最后一行 `All checks passed!` |
| `py -3 -m mypy` | 0 | PASS | `Success: no issues found in 163 source files` |
| 聚焦离线 pytest（17 个文件） | 1 | 146 passed, 1 failed | `test_layered_fusion_resolves_direction_by_majority_vote_not_unanimity` |
| `py -3 -m pytest -q` | 1 | 597 passed, 11 failed, 4 skipped | 3 策略/Ensemble 回归；8 `pandas-ta` 缺失环境失败 |
| `py -3 -m pytest -q tests/integration` | 0 | 2 skipped | 外部依赖路径未执行 |
| `npm --workspace frontend/admin test -- --run` | 0 | 12 files / 37 tests passed | Vitest |
| `npm --workspace frontend/admin run build` | 0 | PASS | Vite production build |
| `git diff --check` | 0 | PASS | 无 whitespace error |

聚焦测试的完整文件集合：

```powershell
py -3 -m pytest -q tests/services/test_runtime_scheduler.py tests/services/test_scheduler_coordination.py tests/services/test_decision_pipeline_runtime.py tests/services/test_signal_ensemble.py tests/services/test_execution_gatekeeper.py tests/services/test_paper_runtime.py tests/services/test_paper_intent_integration.py tests/services/test_binance_gateway.py tests/services/test_exchange_gateway.py tests/services/test_manual_trade_miss_analysis.py tests/services/test_exit_ladder.py tests/services/test_testnet_authorization.py tests/services/test_testnet_acceptance.py tests/repositories/test_config_snapshot_and_decision_event_repository.py tests/repositories/test_decision_snapshot_repository.py tests/api/test_paper_runtime_api.py tests/api/test_execution_rejection_diagnostics.py
```

全量 11 个失败 node id：

```text
tests/services/test_c_plus_strategy.py::TestRegimeRouter::test_identify_uptrend
tests/services/test_c_plus_strategy.py::TestRegimeRouter::test_identify_downtrend
tests/services/test_signal_ensemble.py::test_layered_fusion_resolves_direction_by_majority_vote_not_unanimity
tests/test_pandas_ta_adapter.py::test_unknown_indicator_raises_error
tests/test_pandas_ta_adapter.py::test_insufficient_data_returns_none
tests/test_pandas_ta_adapter.py::test_supertrend_signal_format
tests/test_pandas_ta_adapter.py::test_stoch_rsi_signal_format
tests/test_pandas_ta_adapter.py::test_hma_signal_format
tests/test_pandas_ta_adapter.py::test_mfi_signal_format
tests/test_pandas_ta_adapter.py::test_obv_signal_format
tests/test_pandas_ta_adapter.py::test_all_indicators_callable
```

未运行：`run_testnet_acceptance.py`、`smoke_binance_simulation_path.py`、清仓脚本、任何真实/测试网下单 POST。失败堆栈脱敏摘要见 `FAILURE_EVIDENCE/`。
