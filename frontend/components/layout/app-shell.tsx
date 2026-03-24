import { useEffect, useRef, type ReactNode } from "react";
import { useLocation } from "react-router-dom";

import { Header } from "@/components/layout/header";
import { Sidebar } from "@/components/layout/sidebar";

export function AppShell({ children }: { children: ReactNode }) {
  const location = useLocation();
  const mainRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    mainRef.current?.scrollTo({ top: 0, behavior: "auto" });
  }, [location.pathname]);

  return (
    <div className="flex min-h-screen">
      <Sidebar />
      <div className="min-h-screen flex-1 overflow-hidden">
        <Header />
        <main
          id="app-main-scroll"
          ref={mainRef}
          className="h-[calc(100vh-105px)] overflow-y-auto px-8 py-8 scrollbar-thin"
        >
          {children}
        </main>
      </div>
    </div>
  );
}
