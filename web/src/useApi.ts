import { useCallback, useEffect, useState } from "react";
import { get } from "./api";

/** Fetch `/api/<path>` (null path = skip), optionally re-polling every `refreshMs`. */
export function useApi<T>(path: string | null, refreshMs = 0) {
  const [state, setState] = useState<{ data?: T; error?: string; path?: string | null }>({});
  const [version, setVersion] = useState(0);
  useEffect(() => {
    if (!path) return;
    let alive = true;
    const load = () =>
      get<T>(path).then(
        (data) => alive && setState({ data, path }),
        (e: Error) => alive && setState((s) => ({ ...s, error: e.message, path })),
      );
    void load();
    const id = refreshMs ? setInterval(() => void load(), refreshMs) : undefined;
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [path, refreshMs, version]);
  const fresh = state.path === path;
  return {
    data: fresh ? state.data : undefined,
    error: fresh ? state.error : undefined,
    loading: fresh ? !state.data && !state.error : !!path,
    reload: useCallback(() => setVersion((v) => v + 1), []),
  };
}
