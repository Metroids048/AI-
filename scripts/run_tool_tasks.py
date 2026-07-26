#!/usr/bin/env python3
"""执行工具任务清单"""

import subprocess
import sys


def run_command(cmd, description):
    """运行命令并显示结果"""
    print(f"\n{'=' * 80}")
    print(f"执行: {description}")
    print(f"命令: {cmd}")
    print("=" * 80)

    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)

        if result.stdout:
            print(result.stdout)

        if result.stderr:
            print("错误输出:", result.stderr)

        if result.returncode == 0:
            print(f"✅ {description} - 成功")
        else:
            print(f"❌ {description} - 失败 (退出码: {result.returncode})")

        return result.returncode == 0

    except subprocess.TimeoutExpired:
        print(f"⏱️  {description} - 超时")
        return False
    except Exception as e:
        print(f"❌ {description} - 异常: {e}")
        return False


def main():
    print("\n" + "=" * 80)
    print("Binance自动交易工具任务执行")
    print("=" * 80)

    tasks = [
        # 1. 启用外部持仓共存（如果还没启用）
        ("python scripts/enable_entry_with_external_positions.py", "启用allow_entry_with_unmanaged_positions"),
        # 2. 检查配置状态
        ("python scripts/check_auto_trading_readiness.py", "检查自动交易配置"),
        # 3. 检查风险事件
        ("python scripts/check_risk_events.py", "检查活跃风险事件"),
        # 4. 快速监控
        ("python scripts/quick_monitor.py", "快速监控系统状态"),
    ]

    results = []
    for cmd, desc in tasks:
        success = run_command(cmd, desc)
        results.append((desc, success))

    # 总结
    print("\n" + "=" * 80)
    print("执行总结")
    print("=" * 80)

    for desc, success in results:
        icon = "✅" if success else "❌"
        print(f"{icon} {desc}")

    success_count = sum(1 for _, s in results if s)
    print(f"\n成功: {success_count}/{len(results)}")

    if success_count == len(results):
        print("\n🎉 所有任务执行成功！")
        print("\n建议: 继续监控系统运行情况")
        print("  - 每15-30分钟运行: python scripts/quick_monitor.py")
    else:
        print("\n⚠️  部分任务失败，请检查错误信息")

    return 0 if success_count == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
