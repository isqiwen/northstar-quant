"use client";

import { Alert } from "antd";

export function StorageAlert({ capacity }: { capacity: unknown }) {
  if (!capacity || typeof capacity !== "object") return null;
  const value = capacity as Record<string, unknown>;
  if (value.status !== "LOW" && value.status !== "WARNING") return null;
  const gib = (v: unknown) => (Number(v) / 1024 ** 3).toFixed(1);
  return (
    <Alert
      showIcon
      type={value.status === "LOW" ? "error" : "warning"}
      message={
        value.status === "LOW" ? "原始归档磁盘空间不足" : "原始归档磁盘空间预警"
      }
      description={`剩余 ${gib(value.free_bytes)} GiB，安全预留 ${gib(value.min_free_bytes)} GiB。空间不足时拒绝新增写入；已保存原文与发布版本不会自动删除。请扩容或通过引用检查后的保留操作释放空间。`}
    />
  );
}
