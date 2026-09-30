import { useCallback, useEffect, useState } from "react";

import { ApiError } from "./api/client";

export interface AsyncState<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  reload: () => void;
}

/** Load data for a component; re-runs when `deps` change; ignores stale responses. */
export function useAsync<T>(load: () => Promise<T>, deps: unknown[]): AsyncState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  const reload = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    let current = true;
    setLoading(true);
    load()
      .then((d) => {
        if (current) {
          setData(d);
          setError(null);
        }
      })
      .catch((e: unknown) => {
        if (current) setError(e instanceof ApiError ? e.problem.detail : String(e));
      })
      .finally(() => current && setLoading(false));
    return () => {
      current = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);

  return { data, error, loading, reload };
}
