type Policy = {
  source_label?: string;
  confirmed?: string[];
  pending?: string[];
  research_constraint?: string;
};

export function MinutePolicy({ evidence }: { evidence: unknown }) {
  const report = evidence as
    | {
        supplier_policy?: Policy;
        grid_verified?: boolean;
        expected_records?: number;
        missing_label_count?: number;
        unexpected_label_count?: number;
        rules_pending?: string[];
        session_policy?: {
          rule: string;
          valid_from: string;
          valid_through: string;
        };
        observed_label_clocks?: { clock: string; records: number }[];
      }
    | undefined;
  const policy = report?.supplier_policy;
  if (!policy) return null;
  return (
    <details>
      <summary>分钟生成规则与剩余核验项</summary>
      <div className="muted">{policy.source_label}</div>
      <ul>
        {policy.confirmed?.map((rule) => (
          <li key={rule}>{rule}</li>
        ))}
      </ul>
      {report?.session_policy && (
        <div>
          大商所玉米时段规则（{report.session_policy.valid_from} 至{" "}
          {report.session_policy.valid_through}）： 应有{" "}
          {report.expected_records ?? "—"} 根，缺少{" "}
          {report.missing_label_count ?? "—"} 根， 非预期标签{" "}
          {report.unexpected_label_count ?? "—"} 个。
          {report.grid_verified
            ? "分钟标签完整性已核验。"
            : "分钟标签完整性待核验。"}
        </div>
      )}
      <div>
        {report?.grid_verified
          ? "时段与标签核验已通过；数据可得时间仍需单独验证。"
          : "仍需核实："}
      </div>
      <ul>
        {(report?.rules_pending ?? policy.pending)?.map((rule) => (
          <li key={rule}>{rule}</li>
        ))}
      </ul>
      <div className="muted">{policy.research_constraint}</div>
      {!!report?.observed_label_clocks?.length && (
        <details>
          <summary>实际标签分布（观察结果，不代表应有时段）</summary>
          <div>
            {report.observed_label_clocks
              .map(({ clock, records }) => `${clock}：${records} 条`)
              .join("；")}
          </div>
        </details>
      )}
    </details>
  );
}
