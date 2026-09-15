"use client";
import { useRef, useState } from "react";
import { App, Button } from "antd";
import { useRouter } from "next/navigation";
import type { ExplorerRows } from "../api/generated";
import { explore } from "./api";

export function CompactButton({ result }: { result: ExplorerRows }) {
  const router = useRouter();
  const { message } = App.useApp();
  const request = useRef<{ key: string; id: string } | undefined>(undefined);
  const [busy, setBusy] = useState(false);
  async function submit() {
    if (!request.current || request.current.key !== result.view_id)
      request.current = { key: result.view_id, id: crypto.randomUUID() };
    setBusy(true);
    try {
      const job = await explore("/api/explorer/compactions", {
        request_id: request.current.id,
        dataset: result.dataset,
        scope: result.scope,
        start: result.start,
        end: result.end,
        receipt_ids: result.receipt_ids,
      });
      router.push(`/compactions/${job.compaction_id}`);
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Button
      disabled={result.receipt_ids.length < 2}
      loading={busy}
      onClick={() => void submit()}
    >
      合并固定版本
    </Button>
  );
}
