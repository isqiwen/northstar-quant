"use client";
import { useEffect, useRef, useState } from "react";
import {
  App,
  Button,
  Card,
  Empty,
  Form,
  Input,
  Select,
  Space,
  Tag,
} from "antd";
import Link from "next/link";
import { query } from "../api/client";
import type {
  ExplorerRows,
  ExplorerCoverage,
  ExplorerRange,
} from "../api/generated";
import { useData } from "../../../shared/data";
import { download } from "../../../shared/api";
import { Failure, Heading } from "../../../shared/ui";
import { explore } from "./api";
import { DataPanel } from "./data-panel";
import { CoveragePanel } from "./coverage-panel";
import { VersionsPanel } from "./versions-panel";
import { scopeUrl } from "./states";

type Row = Record<string, unknown>;
function initial(): ExplorerRange {
  const now = new Date();
  const end = now.toLocaleDateString("en-CA", { timeZone: "Asia/Shanghai" });
  now.setDate(now.getDate() - 6);
  return {
    dataset: "15min",
    scope: "",
    start: now.toLocaleDateString("en-CA", { timeZone: "Asia/Shanghai" }),
    end,
    offset: 0,
  };
}
export function Explorer({
  mode,
}: {
  mode: "browse" | "quality" | "versions";
}) {
  const { message } = App.useApp();
  const catalog = useData(query("/api/explorer"));
  const [filter, setFilter] = useState<ExplorerRange>(initial);
  const [exchange, setExchange] = useState("");
  const [product, setProduct] = useState("");
  const [search, setSearch] = useState("");
  const [contracts, setContracts] = useState<Row[]>([]);
  const [contractTotal, setContractTotal] = useState(0);
  const [result, setResult] = useState<ExplorerRows>();
  const [coverage, setCoverage] = useState<ExplorerCoverage>();
  const [versions, setVersions] = useState<Row[]>([]);
  const [versionTotal, setVersionTotal] = useState(0);
  const [versionPage, setVersionPage] = useState(1);
  const [preset, setPreset] = useState<string[]>([]);
  const [loaded, setLoaded] = useState<ExplorerRange>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error>();
  const [exporting, setExporting] = useState(false);
  const generation = useRef(0);
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    setPreset(params.get("receipt") ? [params.get("receipt")!] : []);
    setFilter((f) => ({
      ...f,
      ...Object.fromEntries(
        ["dataset", "scope", "start", "end"].flatMap((k) =>
          params.has(k) ? [[k, params.get(k)!]] : [],
        ),
      ),
    }));
  }, []);
  useEffect(() => {
    let active = true;
    const timer = setTimeout(() => {
      void explore("/api/explorer/contracts", {
        exchange,
        product,
        search,
        offset: 0,
      })
        .then((r) => {
          if (active) {
            setContracts(r.rows);
            setContractTotal(r.total);
          }
        })
        .catch((e) => {
          if (active) setError(e);
        });
    }, 250);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [exchange, product, search]);
  function change(values: Partial<ExplorerRange>) {
    setPreset([]);
    generation.current++;
    setFilter((f) => ({ ...f, ...values }));
    setLoaded(undefined);
    setResult(undefined);
    setCoverage(undefined);
    setVersions([]);
    setError(undefined);
    setBusy(false);
  }
  async function load(offset = 0, ids: string[] = preset, range = filter) {
    const mine = ++generation.current;
    setBusy(true);
    setError(undefined);
    try {
      if (!range.scope) throw new Error("请先选择合约");
      if (mode === "browse") {
        const value = await explore("/api/explorer/query", {
          ...range,
          offset,
          receipt_ids: ids,
          limit: 200,
        });
        if (mine !== generation.current) return;
        setResult(value);
      } else if (mode === "quality") {
        const value = await explore("/api/explorer/coverage", range);
        if (mine !== generation.current) return;
        setCoverage(value);
      } else {
        const value = await explore("/api/explorer/versions", {
          ...range,
          offset,
        });
        if (mine !== generation.current) return;
        setVersions(value.rows);
        setVersionTotal(value.total);
        setVersionPage(offset / 50 + 1);
      }
      setLoaded({ ...range });
    } catch (e) {
      if (mine === generation.current) {
        setError(e as Error);
        setResult(undefined);
        setCoverage(undefined);
        setLoaded(undefined);
        setVersions([]);
      }
    } finally {
      if (mine === generation.current) setBusy(false);
    }
  }
  async function exportRange() {
    if (!result || !loaded) return;
    setExporting(true);
    const fixed = result;
    const range = loaded;
    try {
      const all: Row[] = [];
      for (let offset = 0; offset < fixed.total; offset += 1000) {
        const page = await explore("/api/explorer/export", {
          ...range,
          receipt_ids: fixed.receipt_ids,
          offset,
          limit: 1000,
        });
        if (page.view_id !== fixed.view_id) throw new Error("导出版本发生变化");
        all.push(...page.rows);
      }
      download(
        { ...fixed, rows: all, offset: 0, limit: all.length },
        `northstar-${fixed.scope}-${fixed.view_id.slice(0, 12)}.json`,
      );
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setExporting(false);
    }
  }
  const linkRange = loaded || filter;
  return (
    <>
      <Heading
        title={
          mode === "browse"
            ? "数据浏览"
            : mode === "quality"
              ? "覆盖与质量"
              : "版本与来源"
        }
        description="Tushare · 固定历史版本。筛选仅用于查看，不改变全部自动同步范围。"
        actions={
          <Link href="/sync">
            <Button>查看自动同步</Button>
          </Link>
        }
      />
      <Failure error={catalog.error || error} />
      <Card className="explorer-filters">
        <Form layout="vertical" onFinish={() => void load()}>
          <div className="explorer-controls">
            <Form.Item label="交易所">
              <Select
                aria-label="交易所"
                value={exchange}
                options={[
                  { value: "", label: "全部交易所" },
                  ...(catalog.data?.exchanges || []).map((v) => ({
                    value: v,
                    label: v,
                  })),
                ]}
                onChange={(v) => {
                  setExchange(v);
                  setProduct("");
                  change({ scope: "" });
                }}
              />
            </Form.Item>
            <Form.Item label="品种">
              <Select
                aria-label="品种"
                showSearch
                optionFilterProp="label"
                value={product}
                options={[
                  { value: "", label: "全部品种" },
                  ...Array.from(
                    new Set(
                      (catalog.data?.products || [])
                        .filter((r) => !exchange || r.exchange === exchange)
                        .map((r) => String(r.product)),
                    ),
                  ).map((v) => ({ value: v, label: v })),
                ]}
                onChange={(v) => {
                  setProduct(v);
                  change({ scope: "" });
                }}
              />
            </Form.Item>
            <Form.Item label="合约">
              <Select
                aria-label="合约"
                showSearch
                filterOption={false}
                onSearch={setSearch}
                value={filter.scope || undefined}
                placeholder="搜索合约代码"
                notFoundContent="目录中暂无匹配合约"
                options={contracts.map((r) => ({
                  value: String(r.ts_code),
                  label: `${r.ts_code}${r.kind === "2" ? " · 连续序列" : ""}`,
                }))}
                onChange={(v) => change({ scope: v })}
              />
            </Form.Item>
            <Form.Item label="周期">
              <Select
                aria-label="周期"
                value={filter.dataset}
                options={(catalog.data?.datasets || [])
                  .filter((r) => r.browsable)
                  .map((r) => ({
                    value: String(r.key),
                    label: String(r.label),
                  }))}
                onChange={(v) => {
                  change({ dataset: v });
                }}
              />
            </Form.Item>
            <Form.Item label="开始日期">
              <Input
                aria-label="开始日期"
                type="date"
                value={filter.start}
                onChange={(e) => change({ start: e.target.value })}
              />
            </Form.Item>
            <Form.Item label="结束日期">
              <Input
                aria-label="结束日期"
                type="date"
                value={filter.end}
                onChange={(e) => change({ end: e.target.value })}
              />
            </Form.Item>
          </div>
          <Space wrap>
            {preset.length > 0 && (
              <Tag color="blue">正在查看指定的历史分片版本</Tag>
            )}
            <Button
              type="primary"
              htmlType="submit"
              loading={busy}
              disabled={!filter.scope}
            >
              查询数据
            </Button>
            <span className="muted">
              合约匹配 {contractTotal} 个，最多显示前 50
              个；可输入代码缩小范围。
            </span>
          </Space>
        </Form>
      </Card>
      <Space wrap className="explorer-links">
        {[
          ["/browse", "数据浏览"],
          ["/quality", "覆盖与质量"],
          ["/versions", "版本与来源"],
        ].map(([path, label]) => (
          <Link key={path} href={scopeUrl(path, linkRange)}>
            <Button
              type={
                path ===
                `/${mode === "browse" ? "browse" : mode === "quality" ? "quality" : "versions"}`
                  ? "primary"
                  : "default"
              }
            >
              {label}
            </Button>
          </Link>
        ))}
        <Link href="/sources">来源归档与处理记录</Link>
        <Link href="/datasets">研究快照</Link>
      </Space>
      {!loaded && !busy && !error && (
        <Card>
          <Empty description="选择合约和日期范围，查询已同步的数据" />
        </Card>
      )}
      {mode === "browse" && result && (
        <DataPanel
          result={result}
          exporting={exporting}
          onExport={() => void exportRange()}
          onPage={(offset) => void load(offset, result.receipt_ids, loaded!)}
        />
      )}
      {mode === "quality" && coverage && (
        <CoveragePanel coverage={coverage} linkRange={linkRange} />
      )}
      {mode === "versions" && loaded && (
        <VersionsPanel
          versions={versions}
          linkRange={linkRange}
          versionPage={versionPage}
          versionTotal={versionTotal}
          onPage={(offset) => void load(offset)}
        />
      )}
    </>
  );
}
