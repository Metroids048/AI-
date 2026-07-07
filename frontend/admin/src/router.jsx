import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { NavLink, Outlet, RouterProvider, createBrowserRouter } from "react-router-dom";

import { request } from "./api/client";
import { OpsConsole } from "./pages/OpsConsole";
import { PaperConsole } from "./pages/PaperConsole";
import { ResearchDesk } from "./pages/ResearchDesk";
import { ReviewCenter } from "./pages/ReviewCenter";
import { RiskConsole } from "./pages/RiskConsole";
import { StrategyLibrary } from "./pages/StrategyLibrary";
import { ValidationCenter } from "./pages/ValidationCenter";

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
      { path: "validation", element: <ValidationCenter /> },
      { path: "review", element: <ReviewCenter /> },
      { path: "research", element: <ResearchDesk /> },
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
    <>
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
    </>
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
