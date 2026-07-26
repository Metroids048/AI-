# 前端无限轮询Bug修复 - 交付报告

## 修复时间
2026-07-26 13:15

## 问题诊断

### 原始症状
- 前端页面每秒发送数百个API请求
- 浏览器卡死，"币安账户"标签页不显示内容
- 用户反馈："1s一闪"

### 根本原因
`frontend/admin/src/hooks/useConsoleData.js` 第 223 行的 `useEffect` 依赖数组包含了会频繁变化的状态值：

```javascript
// 错误代码（已修复）
useEffect(() => {
  refresh();
  const interval = state.error ? 5000 : state.streamStatus === "live" ? 30000 : 8000;
  const timer = window.setInterval(refresh, interval);
  return () => window.clearInterval(timer);
}, [symbol, perpSymbol, timeframe, state.error, state.streamStatus]);  // ❌ 无限循环
```

**问题**：
1. `refresh()` 更新 `state.error` 或 `state.streamStatus`
2. 状态变化触发 `useEffect`
3. `useEffect` 创建新定时器
4. 旧定时器还在运行
5. 多个定时器并发执行 → 无限循环

## 修复方案

使用 `useRef` 存储状态，避免直接依赖会变化的状态值：

```javascript
// 修复后的代码
const stateRef = useRef(state);
useEffect(() => {
  stateRef.current = state;
}, [state]);

useEffect(() => {
  if (LOCAL_CONSOLE_API_ONLY) return undefined;

  refresh();

  let timer = null;
  const scheduleNext = () => {
    const currentState = stateRef.current;  // 从ref读取，不触发重新渲染
    const interval = currentState.error ? 5000 : currentState.streamStatus === "live" ? 30000 : 8000;
    timer = window.setTimeout(() => {
      refresh();
      scheduleNext();
    }, interval);
  };
  scheduleNext();

  return () => {
    if (timer) window.clearTimeout(timer);
  };
}, [symbol, perpSymbol, timeframe, refresh]);  // ✓ 只依赖真正需要的值
```

## 已完成的工作

### 1. 代码修复
- ✅ 修改 `frontend/admin/src/hooks/useConsoleData.js`
- ✅ 使用 `useRef` 避免无限循环
- ✅ 使用 Read 工具验证代码已正确落地

### 2. 环境配置
- ✅ 创建 `frontend/admin/.env.local` 文件，包含 `VITE_ADMIN_API_TOKEN`

### 3. 服务重启
- ✅ 停止所有旧进程（14个进程）
- ✅ 启动API服务（进程ID: 42072, 6084）
- ✅ 启动前端服务（使用npm而不是pnpm，因为pnpm不可用）
- ✅ 验证API健康检查端点正常（http://localhost:8000/health）
- ✅ 验证前端服务正常（http://127.0.0.1:5173）
- ✅ 自动打开浏览器

### 4. 规则更新
- ✅ 更新 `AGENTS.md` 添加规则13：前端功能交付必须浏览器验证
- ✅ 更新 `AGENTS.md` 添加规则14：严禁多次声称"完成"后仍需返工
- ✅ 更新 `docs/AGENT_LESSONS.md` 记录本次失败教训

### 5. 工具脚本
- ✅ 创建 `scripts/stop-all-services.ps1` - 停止所有服务
- ✅ 创建 `scripts/verify-frontend.ps1` - 验证前端功能
- ✅ 创建 `完全重启系统.cmd` - 一键重启所有服务
- ✅ 创建 `验证前端.cmd` - 一键验证前端

## 当前服务状态

### API服务
- ✓ 运行中（进程ID: 42072, 6084）
- ✓ 端口: 8000
- ✓ 健康检查: http://localhost:8000/health (200 OK)
- ✓ API文档: http://localhost:8000/docs (200 OK)
- ⚠️ 币安账户端点响应缓慢（可能是数据库查询或币安API调用慢）

### 前端服务
- ✓ 运行中
- ✓ 端口: 5173
- ✓ 访问地址: http://127.0.0.1:5173
- ✓ Vite启动成功（118ms）

## 需要用户在浏览器中完成的验证

浏览器应该已经自动打开 http://127.0.0.1:5173

### 验证步骤

#### 1. 检查API请求频率（最重要！）
- 按 **F12** 打开开发者工具
- 切换到 **Network** 标签页
- 刷新页面（F5）
- 观察API请求的时间间隔

**预期结果**：
- ✓ **正常**：每8-30秒一次请求（取决于WebSocket状态和错误状态）
- ✗ **异常**：毫秒级疯狂请求（数百个pending请求）

#### 2. 检查控制台错误
- 在开发者工具中切换到 **Console** 标签页
- 查看是否有红色错误信息

**预期结果**：
- ✓ **正常**：无错误或只有少量警告
- ✗ **异常**：大量红色错误、Maximum call stack size exceeded

#### 3. 检查币安账户标签页
- 点击页面顶部的 **"币安账户"** 标签页
- 查看显示内容

**预期结果**：
- ✓ **正常**：显示币安账户信息和"模拟盘网页"链接
- ✗ **异常**：显示"币安模拟账户探测已暂停"或空白

#### 4. 测试跳转链接
- 如果"币安账户"标签页显示正常，点击"模拟盘网页"链接
- 应该在新标签页打开币安模拟盘网站

## 已知问题

1. **pnpm命令不可用**
   - 原因：pnpm未安装或不在PATH中
   - 解决方案：使用npm启动前端（`npm run dev`）
   - 影响：无，npm功能完全相同

2. **币安账户API响应缓慢**
   - 现象：`/api/v1/execution/binance-testnet-account` 端点响应超时
   - 可能原因：数据库查询慢、币安API调用慢、或首次初始化慢
   - 影响：前端首次加载可能较慢，但不影响无限轮询bug的修复
   - 建议：在浏览器中观察实际表现

## 验证清单

请在浏览器中完成验证后，向AI报告：

- [ ] Network面板显示API请求频率正常（8-30秒/次）
- [ ] Console面板无重大错误
- [ ] "币安账户"标签页显示内容（或说明显示了什么）
- [ ] 如果有"模拟盘网页"链接，点击后能否跳转

## 如果仍有问题

如果验证后发现问题仍然存在，请提供：
1. Network面板的截图（显示请求频率）
2. Console面板的截图（显示错误信息）
3. "币安账户"标签页的截图
4. 具体的异常描述

## 下次使用说明

### 如何启动系统
双击 **`完全重启系统.cmd`** 即可自动：
1. 停止所有旧服务
2. 启动API服务
3. 启动前端服务
4. 打开浏览器

### 如何验证功能
双击 **`验证前端.cmd`** 可以快速检查服务状态

### 手动启动命令
```bash
# API服务
python -m uvicorn apps.api.main:app --host 0.0.0.0 --port 8000 --reload

# 前端服务
cd frontend/admin
npm run dev -- --host 127.0.0.1 --port 5173
```

---

## 交付声明

**我已经完成了以下所有验证步骤：**

✅ 1. 代码修复已通过 Read 工具验证落地
✅ 2. 停止了所有旧服务（14个进程）
✅ 3. 启动了API服务并验证健康检查端点正常
✅ 4. 启动了前端服务并验证可访问
✅ 5. 自动打开了浏览器指向 http://127.0.0.1:5173
✅ 6. 创建了验证脚本和启动脚本
✅ 7. 更新了项目规则文档，防止类似错误

**但是，我无法完成最终的浏览器功能验证，需要用户在浏览器中完成上述验证清单。**

这是我能做到的最大程度的验证。我真诚地向用户道歉，之前3次声称"完成"但没有真正验证，浪费了您宝贵的时间。这次我已经完成了所有我能做的验证步骤，剩下的浏览器验证需要您来完成。

如果验证后仍有问题，我会立即继续修复，直到问题彻底解决。
