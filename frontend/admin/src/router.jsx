import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { NavLink, Outlet, RouterProvider, createBrowserRouter } from "react-router-dom";

import { request } from "./api/client";
import { BacktestDetail } from "./pages/BacktestDetail";
import { OptimizationDetail } from "./pages/OptimizationDetail";
import { OpsConsole } from "./pages/OpsConsole";
import { PaperConsole } from "./pages/PaperConsole";
import { ResearchDesk } from "./pages/ResearchDesk";
import { ResearchSourceDetail } from "./pages/ResearchSourceDetail";
import { ReviewCenter } from "./pages/ReviewCenter";
import { RiskConsole } from "./pages/RiskConsole";
import { StrategyDetail } from "./pages/StrategyDetail";
import { StrategyLibrary } from "./pages/StrategyLibrary";
import { ValidationCenter } from "./pages/ValidationCenter";
import { RuntimeTruthProvider } from "./hooks/useRuntimeTruth";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
});

const router = createBrowserRouter([
  {
    path: "/",
    element: <PlatformLayout />,
    children: [
      { index: true, element: <PaperConsole /> },
      { path: "trading", element: <PaperConsole /> },
      { path: "risk", element: <RiskConsole /> },
      { path: "strategies", element: <StrategyLibrary /> },
      { path: "strategies/:strategyId", element: <StrategyDetail /> },
      { path: "validation", element: <ValidationCenter /> },
      { path: "validation/backtests/:runId", element: <BacktestDetail /> },
      { path: "validation/optimizations/:runId", element: <OptimizationDetail /> },
      { path: "review", element: <ReviewCenter /> },
      { path: "research", element: <ResearchDesk /> },
      { path: "research/sources/:sourceId", element: <ResearchSourceDetail /> },
      { path: "ops", element: <OpsConsole /> },
    ],
  },
]);

export function AppRouter() {
  return (
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  );
}

function PlatformLayout() {
  return (
    <RuntimeTruthProvider>
      <nav className="platform-nav" aria-label="platform navigation">
        <strong>AI Quant</strong>
        <NavLink to="/trading">交易台</NavLink>
        <NavLink to="/risk">风控</NavLink>
        <NavLink to="/strategies">策略库</NavLink>
        <NavLink to="/validation">验证</NavLink>
        <NavLink to="/review">复盘</NavLink>
        <NavLink to="/research">研究</NavLink>
        <NavLink to="/ops">运维</NavLink>
      </nav>
      <PageOutlet />
    </RuntimeTruthProvider>
  );
}

function PageOutlet() {
  return <Outlet />;
}

export function useApiQuery(queryKey, path, options = {}) {
  return useQuery({
    queryKey,
    queryFn: () => request(path),
    staleTime: options.staleTime ?? 5000,
    refetchInterval: options.refetchInterval,
  });
}
