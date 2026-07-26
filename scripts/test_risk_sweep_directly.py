#!/usr/bin/env python3
"""直接调用risk_profile_sweep验证手动持仓排除逻辑是否真正生效"""

import sys

sys.path.insert(0, ".")

from services.execution.tasks import risk_profile_sweep

print("\n=== 直接运行 risk_profile_sweep() ===\n")
result = risk_profile_sweep()
print(f"结果: {result}")
