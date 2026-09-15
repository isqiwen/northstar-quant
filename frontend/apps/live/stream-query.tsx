"use client";
import { useParams } from "next/navigation";
import { query } from "./api/client";
import { useData } from "../../shared/data";
import { Evidence, Failure, Fields, Heading } from "../../shared/ui";

export function StreamQuery() {
  const { id, queryId } = useParams<{ id: string; queryId: string }>();
  const q = useData(query(`/api/streams/${id}/account-queries/${queryId}`));
  return <>
    <Heading title="固定账户查询" description="按查询身份读取原接收窗口。后续刷新不会替换这里的结果；查询不是账户核对或交易授权。" />
    <Failure error={q.error} />
    {q.data && <>
      <Fields value={{ 查询身份: q.data.query_id, 查询状态: q.data.status,
        来源前缀: q.data.through_sequence, 内容身份: q.data.source_hash }} />
      <Evidence value={q.data.account_observation} title="柜台资金观察（尚未核对）" />
      <Evidence value={q.data.completeness} title="查询内容与缺项" />
      <Evidence value={q.data} title="完整查询证据" />
    </>}
  </>;
}
