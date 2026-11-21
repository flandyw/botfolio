import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";

/** Single-select segmented control; never allows deselecting to empty. */
export function Segmented<T extends string>({
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
    <ToggleGroup
      type="single"
      variant="outline"
      size="sm"
      spacing={0}
      value={value}
      onValueChange={(v) => v && onChange(v as T)}
      aria-label={label}
    >
      {options.map(([v, text, disabled]) => (
        <ToggleGroupItem
          key={v}
          value={v}
          disabled={disabled}
          className="px-2.5 data-[state=on]:bg-primary data-[state=on]:text-primary-foreground"
        >
          {text}
        </ToggleGroupItem>
      ))}
    </ToggleGroup>
  );
}
