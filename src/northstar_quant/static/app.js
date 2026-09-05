"use strict";

const integerFields = new Set(["lookback", "max_lots", "slippage_ticks", "order_lifetime_seconds"]);

async function api(path, payload) {
  const options = payload === undefined ? {} : {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(payload),
  };
  const response = await fetch(path, options);
  const result = await response.json();
  if (!response.ok) throw new Error(result.detail || "操作未完成，请稍后重试。");
  return result;
}

function status(node, message, failed = false) {
  node.textContent = message;
  node.classList.toggle("error", failed);
}

function readConfiguration(form) {
  const values = Object.fromEntries(new FormData(form));
  const snapshotId = values.snapshot_id;
  delete values.snapshot_id;
  for (const key of integerFields) {
    const value = values[key];
    if (!/^[0-9]+$/.test(value)) throw new Error("回看、手数、滑点和有效时间必须是整数。");
    values[key] = Number(value);
    if (!Number.isSafeInteger(values[key])) throw new Error("整数超出支持范围。");
  }
  return {snapshot_id: snapshotId, config: values};
}

const importForm = document.querySelector("#import-form");
const researchForm = document.querySelector("#research-form");
let selectedDataset = null;
let selectionSequence = 0;

function datasetLabel(data) {
  return `${data.exchange} · ${data.symbol} · ${data.trading_day} · ` +
    `${data.session_open} — ${data.session_close} · ${data.bar_count} bars`;
}

function showDataNotice(data) {
  const notices = {
    FINAL_REVISED: ["最终修订数据 · 仅用于探索模拟",
      "信息时钟假设为每根 bar 完成时可见，并非历史上观测到的首次可得时间。不能据此证明当时能够做出相同决策。"],
    SOURCE_DECLARED: ["来源声明 · 未经独立验证",
      "available_at 依据由操作人声明，系统未独立验证它是否为历史首次可得时间。"],
    SYNTHETIC: ["合成示例 · 非真实行情", "仅用于演示和工程验证，不用于评价真实市场中的策略表现。"],
  };
  const notice = document.createElement("aside");
  notice.className = "data-notice";
  const title = document.createElement("strong");
  const description = document.createElement("p");
  [title.textContent, description.textContent] = notices[data.availability_basis];
  const declaration = document.createElement("p");
  declaration.className = "muted";
  declaration.textContent = `声明：${data.availability_note}`;
  notice.append(title, description, declaration);
  document.querySelector("#selected-data-notice").replaceChildren(notice);
}

async function selectDataset(snapshotId) {
  const selection = ++selectionSequence;
  const select = researchForm.elements.snapshot_id;
  const link = document.querySelector("#selected-data-link");
  const reuse = document.querySelector("#reuse-data-metadata");
  selectedDataset = null;
  reuse.hidden = true;
  link.hidden = true;
  document.querySelector("#selected-data-notice").replaceChildren();
  if (!snapshotId) return false;
  const data = await api(`/api/datasets/${encodeURIComponent(snapshotId)}`);
  if (selection !== selectionSequence) return false;
  if (![...select.options].some((option) => option.value === snapshotId)) {
    select.add(new Option(datasetLabel(data), snapshotId));
  }
  select.value = snapshotId;
  selectedDataset = data;
  link.href = `/datasets/${encodeURIComponent(snapshotId)}`;
  link.hidden = false;
  reuse.hidden = false;
  showDataNotice(data);
  return true;
}

if (researchForm) {
  const notice = document.querySelector("#research-status");
  researchForm.elements.snapshot_id.addEventListener("change", async (event) => {
    try {
      await selectDataset(event.target.value);
      status(notice, "");
    } catch (error) {
      status(notice, error.message, true);
    }
  });
  if (researchForm.elements.snapshot_id.value) {
    selectDataset(researchForm.elements.snapshot_id.value).catch((error) => {
      status(notice, error.message, true);
    });
  }
  document.querySelector("#reuse-data-metadata").addEventListener("click", () => {
    if (!selectedDataset) return;
    for (const [key, value] of Object.entries(selectedDataset.import_spec)) {
      const field = importForm.elements.namedItem(key);
      if (field && field.type !== "file") field.value = value;
    }
    importForm.elements.file.value = "";
    status(document.querySelector("#import-status"),
      "已复用合约、时段与来源信息。请选择新文件，并确认交易日、时段与可得时间声明后导入。");
    importForm.scrollIntoView({behavior: "smooth", block: "start"});
  });
}

if (importForm) importForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = importForm.querySelector("button[type=submit]");
  const notice = document.querySelector("#import-status");
  button.disabled = true;
  status(notice, "正在导入、检查完整性并保存数据快照…");
  try {
    const values = Object.fromEntries(new FormData(importForm));
    const file = values.file;
    delete values.file;
    if (file.size > 5 * 1024 * 1024) throw new Error("CSV 文件不得超过 5 MiB。");
    const csv = new TextDecoder("utf-8", {fatal: true}).decode(await file.arrayBuffer());
    const result = await api("/api/import", {csv, spec: values});
    window.location.assign(`/?dataset=${encodeURIComponent(result.snapshot_id)}#research-form`);
  } catch (error) {
    status(notice, error.message, true);
  } finally {
    button.disabled = false;
  }
});

if (researchForm) researchForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = researchForm.querySelector("button[type=submit]");
  const notice = document.querySelector("#research-status");
  button.disabled = true;
  status(notice, "正在逐 bar 运行策略、风险与成交，并保存完整结果…");
  try {
    const result = await api("/api/runs", readConfiguration(researchForm));
    window.location.assign(result.url);
  } catch (error) {
    status(notice, error.message, true);
    button.disabled = false;
  }
});

for (const button of document.querySelectorAll("[data-use-run]")) {
  button.addEventListener("click", async () => {
    const notice = document.querySelector("#research-status");
    try {
      const run = await api(`/api/runs/${button.dataset.useRun}`);
      const form = document.querySelector("#research-form");
      if (!await selectDataset(run.snapshot.id)) return;
      for (const [key, value] of Object.entries(run.config)) form.elements[key].value = value;
      status(notice, "已载入这次研究的数据与完整配置。修改参数后运行会保存新的结果。");
      form.scrollIntoView({behavior: "smooth", block: "start"});
    } catch (error) {
      status(notice, error.message, true);
    }
  });
}

for (const button of document.querySelectorAll("[data-rerun]")) {
  button.addEventListener("click", async () => {
    button.disabled = true;
    const notice = document.querySelector("#research-status");
    status(notice, "正在用相同快照与配置重新计算…");
    try {
      const run = await api(`/api/runs/${button.dataset.rerun}`);
      const repeated = await api("/api/runs", {snapshot_id: run.snapshot.id, config: run.config});
      if (repeated.run_id === run.run_id) {
        status(notice, "复核通过：相同数据、配置与实现产生完全相同的研究身份。");
      } else {
        window.location.assign(repeated.url);
      }
    } catch (error) {
      status(notice, error.message, true);
    } finally {
      button.disabled = false;
    }
  });
}
