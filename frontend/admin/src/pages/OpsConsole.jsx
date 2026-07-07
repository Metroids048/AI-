import { PlatformPlaceholder } from "./ValidationCenter";

export function OpsConsole() {
  return <PlatformPlaceholder eyebrow="Ops / Agent Layer" title="运维控制台" items={["Agent 任务", "通知出站", "依赖健康", "调度器状态"]} />;
}
