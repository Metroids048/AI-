#!/bin/bash
# Pre-commit hook - 防止未测试/未格式化的代码被提交
# 安装: cp scripts/pre-commit-template.sh .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit

set -e

echo "🔍 Running pre-commit checks..."

# 1. 运行关键测试（快速失败）
echo ""
echo "[1/4] Running critical tests..."
python -m pytest tests/test_carry_delta_neutral_fix.py -v --tb=short || {
    echo "❌ Carry delta-neutral tests failed"
    exit 1
}
echo "✅ Critical tests passed"

# 2. 代码格式检查（仅检查 staged 文件）
echo ""
echo "[2/4] Checking code format..."
# 获取所有 staged 的 Python 文件
STAGED_PY_FILES=$(git diff --cached --name-only --diff-filter=ACM | grep '\.py$' | tr '\n' ' ')

if [ -n "$STAGED_PY_FILES" ]; then
    python -m ruff check $STAGED_PY_FILES || {
        echo "❌ Ruff check failed"
        echo "💡 Run: python -m ruff check --fix $STAGED_PY_FILES"
        exit 1
    }
    echo "✅ Code format check passed"
else
    echo "⏭️  No Python files staged"
fi

# 3. 验证关键修复未被回退
echo ""
echo "[3/4] Verifying critical fixes..."
# 验证 confidence_multiplier 修复
if grep -q "confidence_multiplier=0\.0" services/execution/decision_pipeline.py; then
    echo "❌ CRITICAL: decision_pipeline.py contains confidence_multiplier=0.0"
    echo "   This will cause sizing_sentinel rejection!"
    echo "   Line 615 should be: confidence_multiplier=0.5"
    exit 1
fi

# 验证 hedge 方法不在错误位置
if grep -B2 "_create_hedge_order_request" services/execution/paper_runtime.py | grep -q "def _parse_datetime"; then
    echo "❌ CRITICAL: _create_hedge_order_request is nested in _parse_datetime"
    echo "   This will cause AttributeError when creating hedge orders!"
    exit 1
fi

echo "✅ Critical fixes verified"

# 4. 运行快速类型检查（仅检查关键文件）
echo ""
echo "[4/4] Type checking critical files..."
python -m mypy services/execution/decision_pipeline.py --no-error-summary --ignore-missing-imports || {
    echo "⚠️  Type check warnings (not blocking)"
}
echo "✅ Type check completed"

echo ""
echo "✅ All pre-commit checks passed!"
echo ""
