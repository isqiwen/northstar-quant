"use client";
import Link from "next/link";
import { Button, Card, Statistic } from "antd";
import { query } from "../api/client";
import { useData } from "../../../shared/data";
import { Heading, Failure } from "../../../shared/ui";
import { TaskTable } from "./tasks";
export default function ResearchHome() {
  const tasks = useData(query("/api/tasks"), 2000);
  const runs = useData(query("/api/runs"), 5000);
  return (
    <>
      <Heading
        title="研究工作台"
        description="固定输入 · 跟踪运行 · 分析与比较结果"
        actions={
          <Link href="/experiments/new">
            <Button type="primary">新建回测</Button>
          </Link>
        }
      />
      <Failure error={tasks.error || runs.error} />
      <div className="grid-two">
        <Card>
          <Statistic title="近期任务" value={tasks.data?.length ?? "—"} />
        </Card>
        <Card>
          <Statistic
            title="已保存结果（最近 50 条）"
            value={runs.data?.length ?? "—"}
          />
        </Card>
      </div>
      <Card
        title="近期运行"
        extra={<Link href="/experiments">全部运行与比较</Link>}
      >
        <TaskTable rows={(tasks.data || []).slice(0, 10)} />
      </Card>
      <Card title="研究流程">
        <p>
          选择固定数据与配置，提交独立任务；完成后查看报告，在同一数据、风险及成本条件下比较结果，再登记发布候选。
        </p>
        <p className="muted">
          当前回测为单交易日历史模拟，不代表柜台成交或交易授权。
        </p>
      </Card>
    </>
  );
}
