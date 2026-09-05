"use strict";

const integerFields = new Set(["lookback", "max_lots", "slippage_ticks", "order_lifetime_seconds"]);

async function api(path, payload) {
  const options = payload === undefined ? {} : {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(payload),
  };
  const csrf = document.querySelector('meta[name="northstar-csrf"]');
  if (payload !== undefined && csrf) options.headers["X-Northstar-CSRF"] = csrf.content;
  const response = await fetch(path, options);
  const result = await response.json();
  if (!response.ok) {
    const error = new Error(result.detail || "操作未完成，请稍后重试。");
    error.rejectionId = result.rejection_id;
    throw error;
  }
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
  delete values.name;
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

function showDataNotice(data, target = document.querySelector("#selected-data-notice")) {
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
  target.replaceChildren(notice);
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
  status(notice, "正在按原字节接收文件、记录加工并检查发布条件…");
  const key = "northstar.source.import";
  try {
    const values = Object.fromEntries(new FormData(importForm));
    const file = values.file;
    delete values.file;
    if (file.size > 5 * 1024 * 1024) throw new Error("CSV 文件不得超过 5 MiB。");
    const bytes = new Uint8Array(await file.arrayBuffer());
    const digest = await crypto.subtle.digest("SHA-256", bytes);
    const hash = [...new Uint8Array(digest)].map((value) => value.toString(16).padStart(2, "0")).join("");
    const permission = {
      input_kind: values.input_kind,
      use_basis: values.use_basis,
      allow_retention: importForm.elements.allow_retention.checked,
      allow_download: importForm.elements.allow_download.checked,
      upstream_source_id: values.upstream_source_id.trim() || null,
      transformation_note: values.transformation_note.trim() || null,
    };
    for (const name of Object.keys(permission)) delete values[name];
    const declaration = {filename: file.name, source_name: values.source_name, ...permission, spec: values};
    const requestId = workspaceCommand(key, {...declaration, content_hash: hash});
    // Small bounded chunks avoid argument-stack limits without changing any bytes.
    const chunks = [];
    for (let offset = 0; offset < bytes.length; offset += 32768) {
      chunks.push(String.fromCharCode(...bytes.subarray(offset, offset + 32768)));
    }
    const result = await api("/api/import", {
      ...declaration, content_base64: btoa(chunks.join("")), request_id: requestId,
    });
    sessionStorage.removeItem(key);
    window.location.assign(`/attempts/${encodeURIComponent(result.attempt_id)}`);
  } catch (error) {
    status(notice, error.message, true);
    if (error.rejectionId) {
      const link = document.createElement("a");
      link.href = "/sources";
      link.textContent = ` 查看接收前拒绝记录 ${error.rejectionId}`;
      notice.append(link);
    }
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

const configurationForm = document.querySelector("#configuration-form");
const paperCreateForm = document.querySelector("#paper-create-form");
let configurationSequence = 0;

function showConfiguration(configuration) {
  const target = document.querySelector("#configuration-preview");
  const title = document.createElement("p");
  title.className = "muted";
  title.textContent = `拟绑定：${configuration.name} · ${configuration.created_at}。` +
    "以下为已保存的固定内容；左侧编辑表单不会改变它。";
  const details = document.createElement("details");
  const summary = document.createElement("summary");
  summary.textContent = "查看拟绑定的完整配置";
  const content = document.createElement("pre");
  content.textContent = JSON.stringify(configuration, null, 2);
  details.append(summary, content);
  target.replaceChildren(title, details);
}

// Keep a command identity across a lost HTTP response and page refresh. Only a
// successful acknowledgement clears it; retrying cannot consume another bar.
function workspaceCommand(key, payload) {
  const signature = JSON.stringify(payload);
  const previous = sessionStorage.getItem(key);
  const command = previous ? JSON.parse(previous) : null;
  if (command && command.signature === signature) return command.requestId;
  const requestId = crypto.randomUUID();
  sessionStorage.setItem(key, JSON.stringify({signature, requestId}));
  return requestId;
}

const brokerForm = document.querySelector("#broker-query-form");
if (brokerForm) brokerForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = brokerForm.querySelector("button[type=submit]");
  const notice = document.querySelector("#broker-query-status");
  const key = "northstar.broker.query";
  button.disabled = true;
  status(notice, "正在连接模拟柜台并保存有限查询，约需一分钟；不会下单或自动重连…");
  try {
    const payload = Object.fromEntries(new FormData(brokerForm));
    payload.request_id = workspaceCommand(key, payload);
    const result = await api("/api/broker/queries", payload);
    sessionStorage.removeItem(key);
    window.location.assign(`/broker/${encodeURIComponent(result.batch_id)}`);
  } catch (error) {
    status(notice, `${error.message} 同样参数重试会复用查询身份，不重复连接。`, true);
    button.disabled = false;
  }
});

if (configurationForm) configurationForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = configurationForm.querySelector("button[type=submit]");
  const notice = document.querySelector("#configuration-status");
  button.disabled = true;
  status(notice, "正在保存固定策略与 Risk 配置…");
  try {
    const configuration = await api("/api/configurations", {
      name: configurationForm.elements.name.value,
      config: readConfiguration(configurationForm).config,
    });
    const select = paperCreateForm.elements.configuration_id;
    ++configurationSequence;
    if (![...select.options].some((option) => option.value === configuration.configuration_id)) {
      select.add(new Option(`${configuration.name} · ${configuration.created_at} · ` +
        configuration.configuration_id.slice(0, 12), configuration.configuration_id));
    }
    select.value = configuration.configuration_id;
    showConfiguration(configuration);
    status(notice, "配置已保存并选中。已有会话不会改变；创建新会话才会固定这份配置。");
  } catch (error) {
    status(notice, error.message, true);
  } finally {
    button.disabled = false;
  }
});

if (paperCreateForm) {
  let paperSelectionSequence = 0;
  const notice = document.querySelector("#paper-create-status");
  paperCreateForm.elements.configuration_id.addEventListener("change", async (event) => {
    const sequence = ++configurationSequence;
    document.querySelector("#configuration-preview").replaceChildren();
    const identifier = event.target.value;
    if (!identifier) return;
    try {
      const configurations = await api("/api/configurations");
      if (sequence !== configurationSequence) return;
      const selected = configurations.find((item) => item.configuration_id === identifier);
      if (!selected) throw new Error("未找到这份配置，请刷新页面重新选择。");
      showConfiguration(selected);
      status(notice, "");
    } catch (error) {
      status(notice, error.message, true);
    }
  });
  paperCreateForm.elements.snapshot_id.addEventListener("change", async (event) => {
    const sequence = ++paperSelectionSequence;
    const target = document.querySelector("#paper-data-notice");
    target.replaceChildren();
    const identifier = event.target.value;
    if (!identifier) return;
    try {
      const data = await api(`/api/datasets/${encodeURIComponent(identifier)}`);
      if (sequence !== paperSelectionSequence) return;
      showDataNotice(data, target);
      const link = document.createElement("a");
      link.href = `/datasets/${encodeURIComponent(identifier)}`;
      link.textContent = "查看已接受数据的来源与质量";
      target.append(link);
      status(notice, "");
    } catch (error) {
      status(notice, error.message, true);
    }
  });
  paperCreateForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = paperCreateForm.querySelector("button[type=submit]");
    button.disabled = true;
    status(notice, "正在固定输入与配置，创建暂停的独立模拟账户…");
    const key = "northstar.paper.create";
    try {
      const payload = Object.fromEntries(new FormData(paperCreateForm));
      payload.request_id = workspaceCommand(key, payload);
      const result = await api("/api/paper", payload);
      sessionStorage.removeItem(key);
      window.location.assign(`/paper/${encodeURIComponent(result.session_id)}`);
    } catch (error) {
      status(notice, `${error.message} 相同选择重试会复用命令身份，不重复创建。`, true);
      button.disabled = false;
    }
  });
}

const paperAdvance = document.querySelector("#paper-advance");
if (paperAdvance) paperAdvance.addEventListener("click", async () => {
  const notice = document.querySelector("#paper-advance-status");
  const identifier = paperAdvance.dataset.sessionId;
  const key = `northstar.paper.advance.${identifier}`;
  paperAdvance.disabled = true;
  status(notice, "正在核对已提交状态并处理下一条输入…");
  try {
    const requestId = workspaceCommand(key, {});
    await api(`/api/paper/${encodeURIComponent(identifier)}/advance`, {request_id: requestId});
    sessionStorage.removeItem(key);
    window.location.reload();
  } catch (error) {
    status(notice, `${error.message} 重试使用同一命令身份，不会额外推进一条。`, true);
    paperAdvance.disabled = false;
  }
});

const reprocessForm = document.querySelector("#reprocess-form");
if (reprocessForm) reprocessForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const sourceId = reprocessForm.dataset.sourceId;
  const button = reprocessForm.querySelector("button[type=submit]");
  const notice = document.querySelector("#reprocess-status");
  const key = `northstar.source.reprocess.${sourceId}`;
  button.disabled = true;
  status(notice, "正在读取托管原文并记录新的处理尝试…");
  try {
    const spec = Object.fromEntries(new FormData(reprocessForm));
    const requestId = workspaceCommand(key, spec);
    const attempt = await api(`/api/sources/${encodeURIComponent(sourceId)}/reprocess`, {
      spec, request_id: requestId,
    });
    sessionStorage.removeItem(key);
    window.location.assign(`/attempts/${encodeURIComponent(attempt.attempt_id)}`);
  } catch (error) {
    status(notice, `${error.message} 相同参数重试会复用命令身份。`, true);
    button.disabled = false;
  }
});
