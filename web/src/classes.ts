export const cx = (...parts: (string | false | null | undefined)[]) => parts.filter(Boolean).join(" ");

/** Table cell classes shared by every table. */
export const cell = {
  th: "border-b border-rule py-1.5 pr-4 text-left font-normal whitespace-nowrap text-dim last:pr-0",
  td: "border-b border-hair py-1.5 pr-4 align-top last:pr-0",
  right: "text-right num",
};
