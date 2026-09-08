import { useCallback, useEffect, useState } from "react";
import { read } from "./api";
export type Query<T> = { path: string; readonly response?: T };
export function useData<T>(query: Query<T> | null, interval = 0) {
  const path = query?.path ?? null;
  const [state, setState] = useState<{
    data?: T;
    error?: Error;
    loading: boolean;
  }>({ loading: true });
  const [revision, setRevision] = useState(0);
  const refresh = useCallback(() => setRevision((v) => v + 1), []);
  useEffect(() => {
    if (!path) {
      setState({ loading: false });
      return;
    }
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const abort = new AbortController();
    setState({ loading: true });
    const poll = async () => {
      try {
        const data = await read<T>(path, abort.signal);
        if (active) setState({ data, loading: false });
      } catch (e) {
        if (active)
          setState((old) => ({ ...old, error: e as Error, loading: false }));
      } finally {
        if (active && interval) timer = setTimeout(poll, interval);
      }
    };
    void poll();
    return () => {
      active = false;
      abort.abort();
      clearTimeout(timer);
    };
  }, [path, interval, revision]);
  return { ...state, refresh };
}

export function fetchQuery<T>(query: Query<T> | null): Promise<T> {
  if (!query) return Promise.reject(new Error("没有指定查询"));
  return read<T>(query.path);
}
