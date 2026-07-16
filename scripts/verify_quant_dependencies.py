"""验证 quant 依赖是否正确安装并可用。

这是阶段0的验收脚本，确保所有声明的依赖都真正可用。
"""

import sys
from typing import Any


def check_package(import_name: str, display_name: str) -> tuple[bool, str, Any]:
    """检查单个包是否已安装并返回版本信息。

    Returns:
        (success, version, module)
    """
    try:
        module = __import__(import_name)
        version = getattr(module, "__version__", "unknown")
        return True, version, module
    except ImportError as e:
        return False, str(e), None


def verify_pandas_ta() -> bool:
    """验证 pandas-ta 的核心功能。"""
    success, version, ta = check_package("pandas_ta", "pandas-ta")
    if not success:
        print(f"  ✗ pandas-ta 导入失败: {version}")
        return False

    print(f"  ✓ pandas-ta {version}")

    # 验证核心指标函数存在
    required_indicators = [
        "supertrend",
        "stochrsi",
        "hma",
        "ema",
        "rsi",
        "macd",
        "bbands",
        "atr",
    ]

    missing = []
    for indicator in required_indicators:
        if not hasattr(ta, indicator):
            missing.append(indicator)

    if missing:
        print(f"    警告: 缺失指标函数 {missing}")
        return False

    print(f"    ✓ 验证了 {len(required_indicators)} 个核心指标函数")
    return True


def verify_vectorbt() -> bool:
    """验证 vectorbt 的核心功能。"""
    success, version, vbt = check_package("vectorbt", "vectorbt")
    if not success:
        print(f"  ✗ vectorbt 导入失败: {version}")
        return False

    print(f"  ✓ vectorbt {version}")

    # 验证核心类存在
    try:
        if not hasattr(vbt, "Portfolio"):
            raise ImportError("Portfolio is not exported by vectorbt")
        print("    ✓ Portfolio 类可用")
        return True
    except ImportError as e:
        print(f"    ✗ Portfolio 类导入失败: {e}")
        return False


def verify_backtrader() -> bool:
    """验证 backtrader 的核心功能。"""
    success, version, bt = check_package("backtrader", "backtrader")
    if not success:
        print(f"  ✗ backtrader 导入失败: {version}")
        return False

    print(f"  ✓ backtrader {version}")

    # 验证核心类存在
    try:
        import backtrader as bt
        _ = bt.Strategy
        print("    ✓ Strategy 基类可用")
        return True
    except (ImportError, AttributeError) as e:
        print(f"    ✗ Strategy 基类不可用: {e}")
        return False


def verify_freqtrade() -> bool:
    """验证 freqtrade 的核心功能。"""
    success, version, ft = check_package("freqtrade", "freqtrade")
    if not success:
        print(f"  ✗ freqtrade 导入失败: {version}")
        return False

    print(f"  ✓ freqtrade {version}")
    print("    注意: freqtrade 是 GPL-3.0，只能 distilled_research_only")
    print("    不能直接 import freqtrade.strategy 或复制源码")
    return True


def main() -> int:
    """主验证流程。

    Returns:
        0 if all checks pass, 1 otherwise
    """
    print("=" * 60)
    print("验证 quant 依赖安装状态")
    print("=" * 60)

    results = {
        "pandas-ta": verify_pandas_ta(),
        "vectorbt": verify_vectorbt(),
        "backtrader": verify_backtrader(),
        "freqtrade": verify_freqtrade(),
    }

    print("=" * 60)
    print("验证结果汇总:")
    print("-" * 60)

    all_passed = True
    for package, passed in results.items():
        status = "✓ 通过" if passed else "✗ 失败"
        print(f"  {package:20s} {status}")
        if not passed:
            all_passed = False

    print("=" * 60)

    if all_passed:
        print("✓ 所有 quant 依赖验证通过")
        print("\n下一步: 开始实现 pandas_ta_adapter.py (阶段1)")
        return 0
    else:
        print("✗ 部分依赖验证失败，请检查安装")
        return 1


if __name__ == "__main__":
    sys.exit(main())
