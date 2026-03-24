import { useEffect, useRef, type ReactNode } from "react";
import { useLocation } from "react-router-dom";

import { Header } from "@/components/layout/header";

export function AppShell({ children }: { children: ReactNode }) {
  const location = useLocation();
  const mainRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    mainRef.current?.scrollTo({ top: 0, behavior: "auto" });
  }, [location.pathname]);

  return (
    <div className="min-h-screen">
      <Header />
      <main
        id="app-main-scroll"
        ref={mainRef}
        className="mx-auto h-[calc(100vh-64px)] max-w-[1400px] overflow-y-auto px-6 py-6 scrollbar-thin"
      >
        {children}
      </main>
    </div>
  );
}
