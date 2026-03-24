import clsx from "clsx";
import type { ReactNode } from "react";

export function Card({
  children,
  className,
  glow,
}: {
  children: ReactNode;
  className?: string;
  glow?: boolean;
}) {
  return (
    <section className={clsx("panel p-5", glow && "panel-glow", className)}>
      {children}
    </section>
  );
}
