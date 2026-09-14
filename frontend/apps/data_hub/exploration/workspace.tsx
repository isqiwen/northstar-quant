"use client";
import { useEffect, useRef, useState } from "react";
import { App, Button, Card, ConfigProvider, theme, Empty, Space } from "antd";
import Link from "next/link";
import { query } from "../api/client";
import type {
  ExplorerRows,
  ExplorerInstrument,
  ExplorerCoverage,
  ExplorerRange,
} from "../api/generated";
import { useData } from "../../../shared/data";
import { download } from "../../../shared/api";
import { Failure, Heading } from "../../../shared/ui";
import { explore } from "./api";
import { RangeFilters } from "./range-filters";
import { AvailableData } from "./available-data";
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
  const [instrument, setInstrument] = useState<ExplorerInstrument>();
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
    setSearch(params.get("scope") || "");
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
  useEffect(() => {
    let active = true;
    setInstrument(undefined);
    if (!filter.scope) return;
    void explore("/api/explorer/instrument", { scope: filter.scope })
      .then((value) => {
        if (active) setInstrument(value);
      })
      .catch((e) => {
        if (active) setError(e);
      });
    return () => {
      active = false;
    };
  }, [filter.scope]);
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
  async function openPublished(receipt_id: string) {
    const mine = ++generation.current;
    setBusy(true);
    setError(undefined);
    setResult(undefined);
    setLoaded(undefined);
    try {
      const value = await explore("/api/explorer/open", { receipt_id });
      if (mine !== generation.current) return;
      const range = {
        dataset: value.dataset,
        scope: value.scope,
        start: value.start,
        end: value.end,
        offset: 0,
      };
      setFilter(range);
      setPreset(value.receipt_ids);
      setLoaded(range);
      setResult(value);
      setExchange("");
      setProduct("");
      setSearch(value.scope);
      window.history.replaceState(
        null,
        "",
        scopeUrl("/browse", range) +
          `&receipt=${encodeURIComponent(receipt_id)}`,
      );
    } catch (e) {
      if (mine === generation.current) {
        setError(e as Error);
        setResult(undefined);
        setLoaded(undefined);
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
      <ConfigProvider
        theme={
          mode === "browse"
            ? {
                algorithm: theme.darkAlgorithm,
                components: { Table: { headerBg: "#1b2635" } },
                token: {
                  colorBgContainer: "#141b25",
                  colorBgElevated: "#1a2533",
                  colorBgLayout: "#10151d",
                  colorBorderSecondary: "#293545",
                  colorText: "#dbe5ef",
                  colorTextSecondary: "#94a6bc",
                  colorPrimary: "#6a9de8",
                  borderRadiusLG: 6,
                },
              }
            : undefined
        }
      >
        <div className={mode === "browse" ? "market-terminal" : undefined}>
          {mode === "browse" && (
            <AvailableData
              catalog={catalog.data}
              opening={busy}
              selectedReceipt={result?.receipt_ids[0]}
              onOpen={(id) => void openPublished(id)}
            />
          )}
          <RangeFilters
            catalog={catalog.data}
            filter={filter}
            exchange={exchange}
            product={product}
            contracts={contracts}
            contractTotal={contractTotal}
            preset={preset}
            busy={busy}
            onSubmit={() => void load()}
            onChange={change}
            onSearch={setSearch}
            onExchange={(v) => {
              setExchange(v);
              setProduct("");
              change({ scope: "" });
            }}
            onProduct={(v) => {
              setProduct(v);
              change({ scope: "" });
            }}
          />
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
          <div id="explorer-result" className="explorer-result-content">
            {mode === "browse" && filter.scope && (
              <div
                className="period-toolbar"
                role="group"
                aria-label="行情周期"
              >
                <span>历史周期</span>
                {(catalog.data?.datasets || [])
                  .filter((d) => d.browsable)
                  .map((d) => (
                    <Button
                      key={String(d.key)}
                      size="small"
                      type={filter.dataset === d.key ? "primary" : "text"}
                      disabled={
                        busy || !instrument?.periods.includes(String(d.key))
                      }
                      title={
                        instrument?.periods.includes(String(d.key))
                          ? "切换原生周期，保持当前日期范围"
                          : "该合约尚无已发布数据"
                      }
                      onClick={() => {
                        const range = {
                          ...(loaded || filter),
                          dataset: String(d.key),
                          offset: 0,
                        };
                        change(range);
                        void load(0, [], range);
                      }}
                    >
                      {String(d.label)}
                    </Button>
                  ))}
              </div>
            )}
            {!loaded && !busy && !error && (
              <Card>
                <Empty
                  description={
                    mode === "browse"
                      ? "从左侧选择合约，立即查看历史行情"
                      : "选择合约和日期范围，查询已同步的数据"
                  }
                />
              </Card>
            )}
            {mode === "browse" && result?.total === 0 && (
              <Card>
                <p>
                  所选合约和日期没有已发布记录。请从左侧“已有数据”选择一份，自动填入有记录的日期。
                </p>
                <Button
                  onClick={() =>
                    document
                      .querySelector(".explorer-available")
                      ?.scrollIntoView({ behavior: "smooth" })
                  }
                >
                  选择已有数据
                </Button>
              </Card>
            )}
            {mode === "browse" && result && (
              <DataPanel
                key={result.view_id}
                instrumentName={
                  instrument?.scope === result.scope
                    ? instrument.name
                    : undefined
                }
                result={result}
                exporting={exporting}
                onExport={() => void exportRange()}
                onPage={(offset) =>
                  void load(offset, result.receipt_ids, loaded!)
                }
              />
            )}
          </div>
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
        </div>
      </ConfigProvider>
    </>
  );
}
