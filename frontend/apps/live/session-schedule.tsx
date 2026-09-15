"use client";

import { Alert, Button, Card, Upload } from "antd";
import { useRef, useState } from "react";
import type { SessionSchedule } from "./api/generated";
import { Records } from "../../shared/ui";

export function SessionScheduleInput({
  onChange,
}: {
  onChange: (value: SessionSchedule | undefined, ready: boolean) => void;
}) {
  const [value, setValue] = useState<SessionSchedule>();
  const [error, setError] = useState<string>();
  const [name, setName] = useState<string>();
  const generation = useRef(0);
  return (
    <Card title="固定交易时段（可选）">
      <p>
        未提供时只接收 SHFE
        日盘。夜盘需要包含交易日、实际起止时间、可得时间和依据的时段文件。
        时段随会话固定；它不证明账户已经结算，也不授予交易权限。
      </p>
      <Upload
        accept=".json"
        showUploadList={false}
        beforeUpload={async (file) => {
          const current = ++generation.current;
          setValue(undefined);
          onChange(undefined, false);
          setName(undefined);
          setError(undefined);
          try {
            if (file.size > 65536) throw new Error("时段文件不能超过 64 KiB");
            const parsed = JSON.parse(await file.text());
            if (current !== generation.current) return false;
            if (
              !parsed ||
              typeof parsed.source_reference !== "string" ||
              typeof parsed.available_at !== "string" ||
              !Array.isArray(parsed.windows) ||
              !parsed.windows.every(
                (row: Record<string, unknown>) =>
                  row &&
                  [row.trading_day, row.opens_at, row.closes_at].every(
                    (v) => typeof v === "string",
                  ),
              )
            )
              throw new Error("缺少时段依据、可得时间或交易日与起止时间");
            setValue(parsed);
            onChange(parsed, true);
            setName(file.name);
          } catch (e) {
            if (current !== generation.current) return false;
            setError((e as Error).message);
          }
          return false;
        }}
      >
        <Button>读取时段文件</Button>
      </Upload>
      {error && <Alert type="error" title={error} showIcon />}
      {error && (
        <Button
          onClick={() => {
            ++generation.current;
            setError(undefined);
            onChange(undefined, true);
          }}
        >
          清除无效文件
        </Button>
      )}
      {value && (
        <>
          <p>
            {name} · {value.source_reference} · 可得时间 {value.available_at}
          </p>
          <Records
            title="将绑定的时段"
            rows={value.windows}
            rowKey="opens_at"
            columns={[
              { title: "交易日", dataIndex: "trading_day" },
              { title: "开始", dataIndex: "opens_at" },
              { title: "结束", dataIndex: "closes_at" },
            ]}
          />
          <Button
            onClick={() => {
              setValue(undefined);
              ++generation.current;
              onChange(undefined, true);
              setName(undefined);
            }}
          >
            移除时段文件
          </Button>
        </>
      )}
    </Card>
  );
}
