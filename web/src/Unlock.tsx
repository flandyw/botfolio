import { useState } from "react";
import { ApiError, get, savePassword } from "./api";
import { Button, Message } from "./ui";

/** Asks for the dashboard password and keeps it for this browser tab. */
export function Unlock({ onUnlock }: { onUnlock: () => void }) {
  const [value, setValue] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <form
      className="max-w-sm"
      onSubmit={(e) => {
        e.preventDefault();
        setBusy(true);
        get("orders", value).then(
          () => {
            savePassword(value);
            onUnlock();
          },
          (err: Error) => {
            setBusy(false);
            setError(err instanceof ApiError && err.status === 401 ? "That password did not match." : err.message);
          },
        );
      }}
    >
      <h2 className="text-[15px] font-semibold">Dashboard password</h2>
      <p className="mt-1 text-[13px] text-dim">The controls, order journal and news audit are protected.</p>
      <div className="mt-3 flex gap-2">
        <input
          type="password"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          aria-label="Dashboard password"
          autoComplete="current-password"
          className="min-w-0 flex-1 border border-rule bg-transparent px-2 py-1.5 text-[13px] focus:border-ink focus:outline-none"
        />
        <Button type="submit" kind="primary" disabled={busy || !value}>
          Unlock
        </Button>
      </div>
      {error && <Message error>{error}</Message>}
    </form>
  );
}
