"use client";
import { query, mutate } from "./api/client";
import { Card, Tabs, Button } from "antd";
import { useRouter, useParams } from "next/navigation";
import { useData } from "../../shared/data";
import {
  Evidence,
  Failure,
  Fields,
  Heading,
  Identity,
  Records,
  Status,
} from "../../shared/ui";
import { Action } from "./runtime";
export function Broker() {
  const status = useData(query("/api/broker/status"));
  const q = useData(query("/api/broker/queries"), 5000);
  const navigate = useRouter().push;
  const profiles = status.data?.profiles;
  return (
    <>
      <Heading
        title="柜台连接与查询"
        description="连接只由明确的查询操作发起，网页不接收账户密码或柜台地址。"
      />
      <Failure error={status.error || q.error} />
      <Card>
        <Fields
          value={{
            运行环境: profiles?.[0]?.name ?? "—",
            凭据配置: status.data?.credentials?.configured
              ? "已配置"
              : "未配置",
            发单: "未启用",
            查询记录: q.data?.length ?? "—",
          }}
        />
      </Card>
      <Action
        title="发起只读柜台查询"
        path="/api/broker/queries"
        fields={[{ name: "instrument", label: "合约代码" }]}
        onDone={(r) => navigate(`/broker/${r.batch_id || r.query_batch_id}`)}
      />
      <Records
        title="固定查询记录"
        rows={q.data}
        rowKey="batch_id"
        columns={[
          {
            title: "查询",
            dataIndex: "batch_id",
            render: (v) => <Identity value={v} to={`/broker/${v}`} />,
          },
          {
            title: "状态",
            dataIndex: "status",
            render: (v) => <Status value={v} />,
          },
          { title: "合约", dataIndex: "instrument" },
          { title: "开始时间", dataIndex: "created_at" },
        ]}
      />
      <Evidence value={status.data} title="连接配置与能力" />
    </>
  );
}
export function BrokerDetail() {
  const { id } = useParams<{ id: string }>();
  const q = useData(query(`/api/broker/queries/${id}`));
  const baseline = useData(query(`/api/broker/queries/${id}/baseline-context`));
  const ledger = useData(query(`/api/broker/queries/${id}/ledger-context`));
  const funds = useData(query(`/api/broker/queries/${id}/funds-context`));
  const base = baseline.data?.baseline?.baseline_id;
  const refresh = () => {
    baseline.refresh();
    ledger.refresh();
    funds.refresh();
  };
  return (
    <>
      <Heading
        title="固定柜台查询"
        description="入账与核对基于已保存事实，不能在界面手工改写资金和持仓。"
        actions={<Button onClick={refresh}>刷新核对</Button>}
      />
      <Failure
        error={q.error || baseline.error || ledger.error || funds.error}
      />
      {q.data && (
        <Card>
          <Fields
            value={{ 查询: id, 状态: q.data.status, 合约: q.data.instrument }}
          />
        </Card>
      )}
      <Tabs
        items={[
          {
            key: "facts",
            label: "查询证据",
            children: <Evidence value={q.data} />,
          },
          {
            key: "baseline",
            label: "账户基准",
            children: (
              <>
                <Action
                  title="从本次查询建立基准"
                  path="/api/broker/baselines"
                  fixed={{ source_batch_id: id }}
                  onDone={refresh}
                />
                {base && (
                  <Action
                    title="比较账户基准"
                    path="/api/broker/baseline-checks"
                    fixed={{ baseline_id: base, query_batch_id: id }}
                    onDone={refresh}
                  />
                )}
                <Evidence value={baseline.data} />
              </>
            ),
          },
          {
            key: "positions",
            label: "持仓与委托核对",
            children: (
              <>
                {base && (
                  <Action
                    title="登记本次已确认成交"
                    path="/api/broker/position-entries"
                    fixed={{ baseline_id: base, source_batch_id: id }}
                    onDone={refresh}
                  />
                )}
                <Action
                  title="核对持仓"
                  path="/api/broker/position-checks"
                  fixed={{ query_batch_id: id }}
                  fields={[
                    {
                      name: "entry_id",
                      label: "已入账记录",
                      kind: "select",
                      options: ledger.data?.entries?.map((e) => ({
                        label: e.entry_id,
                        value: e.entry_id,
                      })),
                    },
                  ]}
                  onDone={refresh}
                />
                <Action
                  title="核对未决委托"
                  path="/api/broker/order-checks"
                  fields={[
                    {
                      name: "position_check_id",
                      label: "已保存持仓核对",
                      kind: "select",
                      options: ledger.data?.checks?.map((e) => ({
                        label: e.check_id,
                        value: e.check_id,
                      })),
                    },
                  ]}
                  onDone={refresh}
                />
                <Evidence value={ledger.data} />
              </>
            ),
          },
          {
            key: "funds",
            label: "资金与费用",
            children: (
              <>
                {base && (
                  <Action
                    title="登记确认资金事实"
                    path="/api/broker/funds-entries"
                    fixed={{ baseline_id: base, source_batch_id: id }}
                    onDone={refresh}
                  />
                )}
                <Evidence value={funds.data} />
              </>
            ),
          },
        ]}
      />
    </>
  );
}
