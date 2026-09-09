"use client";

import { Card } from "antd";
import { query } from "./api/client";
import { useData } from "../../shared/data";
import { Failure, Fields, Identity } from "../../shared/ui";

export function ProcessingStatus() {
  const queue = useData(query("/api/processing/status"), 5000);
  // A failed refresh must not leave stale counts looking current.
  const status = queue.error ? undefined : queue.data;
  return (
    <Card title="加工队列" loading={queue.loading}>
      <Failure error={queue.error} />
      {status && (
        <>
          <Fields
            value={{
              处理总数: status.total,
              等待处理: status.pending,
              正在处理: status.running,
              已发布尝试: status.published,
              失败尝试: status.failed,
              最长排队秒数: status.oldest_pending_seconds ?? "无排队任务",
              统计时间: status.observed_at,
            }}
          />
          {status.oldest_pending_id && (
            <p>
              最早排队任务：
              <Identity
                value={status.oldest_pending_id}
                to={`/attempts/${status.oldest_pending_id}`}
              />
            </p>
          )}
          <p>
            统计全部处理尝试，每 5
            秒刷新；运行中记录不代表执行进程健康，失败记录需查看原因后显式重试。
          </p>
        </>
      )}
    </Card>
  );
}
