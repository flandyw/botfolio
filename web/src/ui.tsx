import type { ReactNode } from "react";

import { cx } from "./classes";

/** One-of-many text choice. The current option is underlined; nothing can be deselected. */
export function Choice<T extends string>({
  value,
  onChange,
  options,
  label,
}: {
  value: T;
  onChange: (v: T) => void;
  options: readonly (readonly [T, string] | readonly [T, string, boolean])[];
  label: string;
}) {
  return (
    <div role="radiogroup" aria-label={label} className="flex items-center gap-3">
      {options.map(([v, text, disabled]) => (
        <button
          key={v}
          type="button"
          role="radio"
          aria-checked={v === value}
          disabled={disabled}
          onClick={() => onChange(v)}
          className={cx(
            "border-b pb-px text-[13px] disabled:text-faint disabled:line-through",
            v === value ? "border-ink text-ink" : "border-transparent text-dim hover:text-ink",
          )}
        >
          {text}
        </button>
      ))}
    </div>
  );
}

export function Section({
  title,
  note,
  aside,
  children,
}: {
  title: string;
  note?: ReactNode;
  aside?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="min-w-0">
      <div className="mb-2 flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h2 className="text-[15px] font-semibold">{title}</h2>
        {note && <span className="text-[13px] text-dim">{note}</span>}
        {aside && <div className="ml-auto">{aside}</div>}
      </div>
      {children}
    </section>
  );
}

/** A labeled figure on the account and worker summary lines. */
export function Figure({ label, children, large }: { label: string; children: ReactNode; large?: boolean }) {
  return (
    <div className="min-w-0">
      <dt className="text-[13px] text-dim">{label}</dt>
      <dd className={cx("mt-0.5", large ? "num text-[32px] leading-9 font-medium" : "text-[15px]")}>{children}</dd>
    </div>
  );
}

export function Message({ error, children }: { error?: boolean; children: ReactNode }) {
  return <p className={cx("py-2 text-[13px]", error ? "text-down" : "text-dim")}>{children}</p>;
}

/** Rectangular text button. `primary` is the main action; `danger` is the emergency stop. */
export function Button({
  children,
  onClick,
  disabled,
  kind,
  type = "button",
}: {
  children: ReactNode;
  onClick?: () => void;
  disabled?: boolean;
  kind?: "primary" | "danger";
  type?: "button" | "submit";
}) {
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className={cx(
        "border px-3 py-1.5 text-[13px] font-medium disabled:cursor-not-allowed disabled:border-hair disabled:bg-transparent disabled:text-faint",
        kind === "primary"
          ? "border-ink bg-ink text-bg hover:opacity-85"
          : kind === "danger"
            ? "border-down text-down hover:bg-down hover:text-bg"
            : "border-rule hover:border-ink",
      )}
    >
      {children}
    </button>
  );
}

export function SymbolLink({ symbol, onOpen }: { symbol: string; onOpen: (s: string) => void }) {
  return (
    <button
      type="button"
      onClick={() => onOpen(symbol)}
      title={`Open ${symbol} chart`}
      className="font-medium underline decoration-rule underline-offset-4 hover:decoration-ink"
    >
      {symbol}
    </button>
  );
}
