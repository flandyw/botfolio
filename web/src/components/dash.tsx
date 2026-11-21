import type { ReactNode } from "react";
import { cn } from "cn";
import { Badge } from "@/components/ui/badge";
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

type Tone = "up" | "down" | "accent" | "warn";

const toneText: Record<Tone, string> = {
  up: "text-up",
  down: "text-down",
  accent: "text-primary",
  warn: "text-warn",
};
const toneBar: Record<Tone, string> = {
  up: "bg-up",
  down: "bg-down",
  accent: "bg-primary",
  warn: "bg-warn",
};

export function Kpi({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: Tone }) {
  return (
    <Card size="sm" className="relative gap-1">
      <span className={cn("absolute inset-x-0 top-0 h-0.5", tone ? toneBar[tone] : "bg-border")} />
      <CardContent className="space-y-1">
        <div className="text-[11px] font-medium tracking-wider text-muted-foreground uppercase">{label}</div>
        <div className={cn("num text-2xl font-semibold tracking-tight", tone && tone !== "accent" && toneText[tone])}>
          {value}
        </div>
        {sub && <div className="truncate text-xs text-muted-foreground">{sub}</div>}
      </CardContent>
    </Card>
  );
}

export function KpiRow({ children }: { children: ReactNode }) {
  return <section className="grid grid-cols-[repeat(auto-fit,minmax(190px,1fr))] gap-3">{children}</section>;
}

export function Panel({
  title,
  description,
  action,
  children,
  className,
}: {
  title?: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <Card className={cn("min-w-0", className)}>
      {(title || action) && (
        <CardHeader>
          {title && <CardTitle className="flex flex-wrap items-center gap-2">{title}</CardTitle>}
          {description && <CardDescription className="text-xs">{description}</CardDescription>}
          {action && <CardAction className="flex flex-wrap items-center gap-2">{action}</CardAction>}
        </CardHeader>
      )}
      <CardContent className="min-w-0">{children}</CardContent>
    </Card>
  );
}

const tagTone: Record<string, string> = {
  up: "bg-up/15 text-up",
  down: "bg-down/15 text-down",
  accent: "bg-primary/15 text-primary",
  warn: "bg-warn/15 text-warn",
};

export function Tag({ tone, children }: { tone?: string; children: ReactNode }) {
  const t =
    tone === "long" || tone === "buy"
      ? "up"
      : tone === "short" || tone === "sell"
        ? "down"
        : tone;
  return (
    <Badge variant="secondary" className={cn("font-medium", t && tagTone[t])}>
      {children}
    </Badge>
  );
}

export function Notice({ error, children }: { error?: boolean; children: ReactNode }) {
  return (
    <div
      className={cn(
        "rounded-xl border border-dashed px-5 py-8 text-center text-sm text-muted-foreground",
        error && "border-down/40 bg-down/5 text-down",
      )}
    >
      {children}
    </div>
  );
}

export function LinkButton({ onClick, children, title }: { onClick: () => void; children: ReactNode; title?: string }) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      className="text-left font-medium underline-offset-4 hover:text-primary hover:underline"
    >
      {children}
    </button>
  );
}

export const upDown = (v: number | null | undefined) => ((v ?? 0) >= 0 ? "text-up" : "text-down");
