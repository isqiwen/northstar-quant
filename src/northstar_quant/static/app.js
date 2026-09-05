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
    document.querySelector("#research-form [name=snapshot_id]").value = result.snapshot_id;
    status(notice, `已保存 ${result.bar_count} 个 bars。数据快照已填入右侧，可以运行研究。`);
    document.querySelector("#research-form").scrollIntoView({behavior: "smooth", block: "nearest"});
  } catch (error) {
    status(notice, error.message, true);
  } finally {
    button.disabled = false;
  }
});

const researchForm = document.querySelector("#research-form");
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
      form.elements.snapshot_id.value = run.snapshot.id;
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
