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
  const runtime = document.querySelector('meta[name="northstar-live-runtime"]');
  if (payload !== undefined && runtime) options.headers["X-Live-Runtime-ID"] = runtime.content;
  const response = await fetch(path, options);
  const result = await response.json();
  if (!response.ok) {
    const error = new Error(result.detail || "操作未完成，请稍后重试。");
    error.rejectionId = result.rejection_id;
    if (result.status === "UNKNOWN") {
      for (const button of document.querySelectorAll("button")) button.disabled = true;
      const notice = document.createElement("p");
      notice.textContent = `命令 ${result.request_id} 结果未知，已禁用本页操作。`;
      const lookup = document.createElement("a");
      lookup.textContent = "按固定命令身份只读查询结果";
      lookup.href = `/live/commands/${encodeURIComponent(result.request_id)}`;
      notice.append(lookup);
      document.querySelector("main").prepend(notice);
    }
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

document.querySelectorAll("[data-broker-local]").forEach((button) => {
  button.addEventListener("click", async () => {
    const queryId = button.dataset.queryBatchId;
    const establishing = button.dataset.brokerLocal === "establish";
    const payload = establishing ? {source_batch_id: queryId} : {
      baseline_id: button.dataset.baselineId,
      query_batch_id: queryId,
    };
    const key = `northstar.broker.${button.dataset.brokerLocal}.${queryId}`;
    const notice = document.querySelector("#broker-baseline-status");
    button.disabled = true;
    status(notice, establishing ? "正在固定已保存的观察，不连接柜台…" :
      "正在比较两份已保存的观察，不连接柜台…");
    try {
      payload.request_id = workspaceCommand(key, payload);
      await api(establishing ? "/api/broker/baselines" : "/api/broker/baseline-checks", payload);
      sessionStorage.removeItem(key);
      window.location.reload();
    } catch (error) {
      status(notice, `${error.message} 重试复用同一命令，不会连接柜台或重复写入。`, true);
      button.disabled = false;
    }
  });
});

document.querySelectorAll("[data-broker-ledger]").forEach((button) => {
  button.addEventListener("click", async () => {
    const queryId = button.dataset.queryBatchId;
    const ingesting = button.dataset.brokerLedger === "ingest";
    const payload = ingesting ? {
      baseline_id: button.dataset.baselineId,
      source_batch_id: queryId,
    } : {entry_id: button.dataset.entryId, query_batch_id: queryId};
    const key = `northstar.broker.positions.${button.dataset.brokerLedger}.${queryId}`;
    const notice = document.querySelector("#broker-ledger-status");
    button.disabled = true;
    status(notice, ingesting ? "正在记录已保存的成交并推导数量，不连接柜台…" :
      "正在比较固定账簿与独立查询，不修改账簿或连接柜台…");
    try {
      payload.request_id = workspaceCommand(key, payload);
      await api(ingesting ? "/api/broker/position-entries" : "/api/broker/position-checks", payload);
      sessionStorage.removeItem(key);
      window.location.reload();
    } catch (error) {
      status(notice, `${error.message} 重试复用同一命令，不会连接柜台或重复入账。`, true);
      button.disabled = false;
    }
  });
});

document.querySelectorAll("[data-broker-funds]").forEach((button) => {
  button.addEventListener("click", async () => {
    const payload = {baseline_id: button.dataset.baselineId, source_batch_id: button.dataset.queryBatchId};
    const key = `northstar.broker.funds.${payload.source_batch_id}`;
    const notice = document.querySelector("#broker-funds-status");
    button.disabled = true;
    status(notice, "正在固定已有资金观察与相邻区间，不连接柜台…");
    try {
      payload.request_id = workspaceCommand(key, payload);
      const result = await api("/api/broker/funds-entries", payload);
      sessionStorage.removeItem(key);
      window.location.assign(`/broker/funds/${encodeURIComponent(result.entry_id)}`);
    } catch (error) {
      status(notice, `${error.message} 重试保持原命令，不重复加总累计金额。`, true);
      button.disabled = false;
    }
  });
});

document.querySelectorAll("[data-broker-orders]").forEach((button) => {
  button.addEventListener("click", async () => {
    const payload = {position_check_id: button.dataset.positionCheckId};
    const key = `northstar.broker.orders.${payload.position_check_id}`;
    const notice = document.querySelector("#broker-orders-status");
    button.disabled = true;
    status(notice, "正在核对已保存的委托与逐笔成交，不连接柜台或修改入账…");
    try {
      payload.request_id = workspaceCommand(key, payload);
      await api("/api/broker/order-checks", payload);
      sessionStorage.removeItem(key);
      window.location.reload();
    } catch (error) {
      status(notice, `${error.message} 重试复用同一命令，不连接柜台或重复核对。`, true);
      button.disabled = false;
    }
  });
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
    if (reprocessForm.dataset.inputKind === "CTP_CALLBACK_SEGMENT") {
      if (!/^[0-9]+$/.test(spec.through_sequence) ||
          !Number.isSafeInteger(Number(spec.through_sequence))) {
        throw new Error("固定前缀身份无效，请重新打开处理详情；不能更换原文。");
      }
      spec.through_sequence = Number(spec.through_sequence);
    }
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

const streamCreateForm = document.querySelector("#stream-create-form");
if (streamCreateForm) streamCreateForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = streamCreateForm.querySelector("button[type=submit]");
  const notice = document.querySelector("#stream-create-status");
  const key = "northstar.stream.create";
  button.disabled = true;
  status(notice, "正在固定范围并启动只读接收与影子预热；不会报单或撤单…");
  try {
    const values = Object.fromEntries(new FormData(streamCreateForm));
    if (!/^[0-9]+$/.test(values.duration_seconds)) {
      throw new Error("接收时长必须为 60–7200 秒的整数。");
    }
    const duration = Number(values.duration_seconds);
    if (!Number.isSafeInteger(duration) || duration < 60 || duration > 7200) {
      throw new Error("接收时长必须为 60–7200 秒的整数。");
    }
    const payload = {
      query_batch_id: values.query_batch_id,
      configuration_id: values.configuration_id,
      duration_seconds: duration,
      allow_retention: streamCreateForm.elements.allow_retention.checked,
      use_basis: values.use_basis.trim(),
    };
    payload.request_id = workspaceCommand(key, payload);
    const result = await api("/api/streams", payload);
    sessionStorage.removeItem(key);
    window.location.assign(`/streams/${encodeURIComponent(result.stream_id)}`);
  } catch (error) {
    status(notice, `${error.message} 同样参数重试复用原命令，不重复建立连接。`, true);
    button.disabled = false;
  }
});
