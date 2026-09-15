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
      <div>仍需核实：</div>
      <ul>
        {policy.pending?.map((rule) => (
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
