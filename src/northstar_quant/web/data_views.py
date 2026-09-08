from __future__ import annotations

import json
from html import escape
from typing import cast

from northstar_quant.web.html import _field, _rows, _table, _text
from northstar_quant.web.requests import _object

_CSV_COLUMNS = "event_time,available_at,source_record_id,open,high,low,close,volume"


_INPUT_KIND_LABELS = {
    "RECEIVED_CSV": "实际收到的 CSV（不宣称供应商原文）",
    "CONVERTED_CSV": "外部转换后 CSV",
    "CTP_CALLBACK_SEGMENT": "已保存 CTP 回调前缀 JSON（不是 CSV 或网络原文）",
}


_PROCESS_LABELS = {
    "PENDING": "等待处理",
    "RUNNING": "正在处理",
    "FAILED": "失败",
    "PUBLISHED": "已发布",
    "RECEIVED": "已接收",
    "VALIDATING": "检查处理参数",
    "PARSING": "解析文件",
    "IMPORTING": "写入观测",
    "QUALITY": "质量检查",
    "PUBLISHING": "发布快照",
}


def _spec_fields(values: dict[str, object] | None = None) -> str:
    specification = {} if values is None else values
    fields = "".join(
        _field(name, label, specification.get(name, default), placeholder=placeholder)
        for name, label, default, placeholder in (
            ("exchange", "交易所", "", "SHFE"),
            ("product", "品种", "", "RB"),
            ("symbol", "合约代码", "", "RB2605"),
            ("timezone", "交易所时区", "Asia/Shanghai", ""),
            ("currency", "币种", "CNY", ""),
            ("quantity_unit", "报价单位", "TON", ""),
            ("price_tick", "最小价格变动", "", "1"),
            ("multiplier", "每手合约乘数", "", "10"),
            ("trading_day", "交易日", "", "2026-01-07"),
            ("session_open", "时段开始（UTC）", "", "2026-01-07T01:00:00Z"),
            ("session_close", "时段结束（UTC）", "", "2026-01-07T03:30:00Z"),
            ("source_name", "数据来源标识（英文）", "", "my-market-export"),
            ("source_reference", "出处（来源地址或文件说明）", "", "数据提供方及下载地址"),
        )
    )
    options = ['<option value="">请选择实际依据</option>']
    for value, label in (
        ("SOURCE_DECLARED", "来源声明（操作人填写，未经独立验证）"),
        ("FINAL_REVISED", "最终修订数据（仅探索模拟）"),
        ("SYNTHETIC", "合成示例（非真实行情）"),
    ):
        selected = " selected" if specification.get("availability_basis") == value else ""
        options.append(f'<option value="{value}"{selected}>{label}</option>')
    return f"""<details open><summary>合约、时段与来源参数</summary>
<div class="fields">{fields}</div></details>
<label class="availability-field">历史可得时间依据<select name="availability_basis" required>
{"".join(options)}</select></label><label>可得时间说明<textarea name="availability_note" rows="3"
required placeholder="说明 available_at 的出处；最终修订数据须说明可见时间仅为假设。"
>{_text(specification.get("availability_note", ""))}</textarea></label>"""


def _source_list(sources: list[dict[str, object]]) -> str:
    rows = [
        f'<tr><td><a href="/sources/{_text(source["source_id"])}">{_text(source["filename"])}</a>'
        f'<br><span class="muted">{_text(source["source_name"])}</span></td>'
        f"<td>{_text(_INPUT_KIND_LABELS.get(str(source['input_kind']), source['input_kind']))}</td>"
        f"<td>{_text(source['byte_count'])}</td><td>{_text(source['received_at'])}</td>"
        f"<td>{_text(source['file_status'])}</td>"
        f"<td>{'允许本机下载' if source['allow_download'] else '不允许下载'}</td></tr>"
        for source in sources
    ]
    return _table(["收到的文件", "输入起点", "字节数", "接收时间", "归档状态", "下载权限"], rows)


def _attempt_list(attempts: list[dict[str, object]]) -> str:
    rows = []
    for attempt in attempts:
        snapshot = attempt["snapshot_id"]
        product = (
            "—"
            if snapshot is None
            else (f'<a href="/datasets/{_text(snapshot)}">查看已发布数据</a>')
        )
        rows.append(
            f'<tr><td><a href="/attempts/{_text(attempt["attempt_id"])}">'
            f"{_text(attempt['created_at'])}</a></td>"
            f"<td>{_text(_PROCESS_LABELS.get(str(attempt['status']), attempt['status']))}</td>"
            f"<td>{_text(_PROCESS_LABELS.get(str(attempt['stage']), attempt['stage']))}</td>"
            f'<td class="wrap-cell">{_text(attempt["error"] or "—")}</td><td>{product}</td></tr>'
        )
    return _table(["处理尝试", "结果", "已到达阶段", "原因", "发布结果"], rows)


def _sources_workspace(
    sources: list[dict[str, object]],
    attempts: list[dict[str, object]],
    rejections: list[dict[str, object]],
) -> str:
    rejected_rows = [
        f"<tr><td>{_text(item['created_at'])}</td>"
        f'<td class="wrap-cell">{_text(item["reason"])}</td>'
        f"<td>{_text(item['rejection_id'])}</td></tr>"
        for item in rejections
    ]
    return f"""<section class="intro"><p class="eyebrow">SOURCE LIBRARY</p>
<h1>原文留存，处理有据。</h1><p>查看实际收到的文件、处理失败与发布结果；不会把未发布材料列为可研究数据。</p>
<div class="actions"><a class="button" href="/#import-form">上传并处理新文件</a></div></section>
<section class="panel"><div class="section-title"><h2>托管来源</h2>
<span class="muted">最近 50 份 · 本机不可变归档</span></div>{_source_list(sources)}</section>
<section class="panel"><div class="section-title"><h2>加工与发布尝试</h2>
<span class="muted">最近 50 次 · 失败记录也保留</span></div>{_attempt_list(attempts)}</section>
<section class="panel"><h2>接收前拒绝</h2><p class="muted">仅保留有界拒绝原因和命令身份，
不保存未获准的内容；传输格式或浏览器会话校验失败不创建来源。</p>
{_table(["时间", "拒绝原因", "记录身份"], rejected_rows)}</section>"""


def _source_page(source: dict[str, object]) -> str:
    is_stream = source["input_kind"] == "CTP_CALLBACK_SEGMENT"
    download_label = "下载托管回调 JSON（含账户信息）" if is_stream else "按原字节下载收到的文件"
    download = (
        f'<a class="button secondary" href="/api/sources/{_text(source["source_id"])}/download">'
        f"{download_label}</a>"
        if source["allow_download"]
        else ""
    )
    upstream = source["upstream_source_id"]
    upstream_link = (
        '<p class="muted">未关联托管上游原文；不补造供应商原文或转换血缘。</p>'
        if upstream is None
        else f'<p><a href="/sources/{_text(upstream)}">查看已关联的上游来源</a></p>'
    )
    if is_stream:
        stream_ids = {
            str(identifier)
            for attempt in _rows(source["attempts"])
            if (identifier := _object(attempt["parameters"]).get("stream_id")) is not None
        }
        upstream_link = "".join(
            f'<p><a href="/streams/{_text(identifier)}">返回固定前缀的来源流</a></p>'
            for identifier in sorted(stream_ids)
        )
        upstream_link += (
            '<p class="muted">这是从已保存 SDK 回调导出的 JSON，不是 CSV 或供应商网络原文。'
            "文件可能含账户 TD 回调和私有身份；本机下载许可不是对外分享许可。</p>"
        )
    properties = (
        ("来源标识", source["source_name"]),
        ("收到的文件名", source["filename"]),
        ("输入起点", _INPUT_KIND_LABELS.get(str(source["input_kind"]), source["input_kind"])),
        ("原文字节摘要", source["content_hash"]),
        ("原文字节数", source["byte_count"]),
        ("归档时间（非行情首次接收）" if is_stream else "接收时间", source["received_at"]),
        ("归档状态", source["file_status"]),
        ("用途与留存依据", source["use_basis"]),
        ("获准留存", "是" if source["allow_retention"] else "否"),
        ("本机下载", "允许" if source["allow_download"] else "不允许"),
        ("转换说明", source["transformation_note"] or "无"),
        ("来源身份", source["source_id"]),
    )
    description = "".join(f"<dt>{label}</dt><dd>{_text(value)}</dd>" for label, value in properties)
    references = _text(
        json.dumps(
            {"products": source["products"], "usages": source["usages"]},
            ensure_ascii=False,
            indent=2,
        )
    )
    description_note = (
        "托管已保存回调的固定 JSON；回调首次接收时刻不会被归档时间替换。"
        if is_stream
        else "托管的是本次实际收到的内容，不以摘要存在代替原文可读。"
    )
    return f"""<section class="intro"><a class="back" href="/sources">← 返回来源与处理</a>
<p class="eyebrow">RECEIVED SOURCE</p><h1>{_text(source["filename"])}</h1>
<p>{description_note}</p>
<div class="actions">{download}</div></section>
<section class="panel"><h2>固定来源与使用声明</h2><dl class="identity">{description}</dl>
{upstream_link}<p class="muted">用途依据由操作者声明，不代表系统已核实第三方授权。
本机下载许可不等于对外再分发许可；当前不提供删除或任意服务器路径访问。</p></section>
<section class="panel"><h2>处理尝试</h2>{_attempt_list(_rows(source["attempts"]))}</section>
<section class="panel"><h2>已发布产物与运行引用</h2><p class="muted">这是当前引用关系，
不会回写历史研究的固定证据或结果身份。</p><pre>{references}</pre></section>"""


def _attempt_page(attempt: dict[str, object]) -> str:
    source = _object(attempt["source"])
    parameters = _object(attempt["parameters"])
    is_stream = source["input_kind"] == "CTP_CALLBACK_SEGMENT"
    error = (
        ""
        if not attempt["error"]
        else f'<aside class="data-notice error">{_text(attempt["error"])}</aside>'
    )
    snapshot = attempt["snapshot_id"]
    product = (
        '<p class="muted">尚无已发布快照，不能用于研究。原文和失败证据仍保留。</p>'
        if snapshot is None
        else f'<div class="actions"><a class="button" '
        f'href="/datasets/{_text(snapshot)}">查看已发布数据</a>'
        f'<a class="button secondary" href="/datasets/{_text(snapshot)}">'
        "查看质量与数据详情</a></div>"
    )
    identities = (
        ("输入来源", source["filename"]),
        ("原文字节摘要", source["content_hash"]),
        ("处理尝试", attempt["attempt_id"]),
        ("命令身份", attempt["request_id"]),
        ("前次尝试", attempt["retry_of"] or "首次处理"),
        ("处理实现摘要", attempt["implementation_hash"]),
        ("创建时间", attempt["created_at"]),
        ("更新时间", attempt["updated_at"]),
    )
    identity_text = "".join(
        f"<dt>{label}</dt><dd>{_text(value)}</dd>" for label, value in identities
    )
    quality = _text(json.dumps(attempt["quality"], ensure_ascii=False, indent=2))
    exact_parameters = _text(json.dumps(parameters, ensure_ascii=False, indent=2))
    if is_stream:
        fields = f"""<input type="hidden" name="stream_id" value="{_text(parameters["stream_id"])}">
<input type="hidden" name="through_sequence" value="{_text(parameters["through_sequence"])}">
<p>固定前缀 1–{_text(parameters["through_sequence"])} ·
<a href="/streams/{_text(parameters["stream_id"])}">查看来源流与其他尝试</a></p>
<label>首分钟起点（UTC）<input type="text" name="session_open" maxlength="40" required
value="{_text(parameters.get("session_open") or "")}" placeholder="YYYY-MM-DDTHH:MM:00Z"></label>
<label>最后一分钟完成边界（UTC）<input type="text" name="session_close" maxlength="40" required
value="{_text(parameters.get("session_close") or "")}" placeholder="YYYY-MM-DDTHH:MM:00Z">
</label>"""
        reprocess_note = (
            "只调整所选完整分钟的 UTC 起止范围；来源流和前缀固定，不能改成后来收到的数据。"
            "从同一托管 JSON 回调重新加工，不复制近期影子步骤，不回写已运行影子目标或补做旧决策。"
            "新的范围仍须通过质量检查；不能以重处理消除源缺口或补造分钟。"
        )
        kind = ' data-input-kind="CTP_CALLBACK_SEGMENT"'
    else:
        fields = _spec_fields(parameters)
        reprocess_note = (
            "读取同一份托管原文，创建新的处理尝试。不替换原文、权限、历史可得时间声明或已发布事实。"
            "相同内容与参数可复用已确认产物；文件内容本身错误时，请上传修正后的新文件。"
        )
        kind = ""
    return f"""<section class="intro"><a class="back" href="/sources/{_text(source["source_id"])}">
← 返回来源详情</a><p class="eyebrow">PROCESSING ATTEMPT</p>
<h1>{_text(source["filename"])} · 处理尝试</h1>
<p>{_text(_PROCESS_LABELS.get(str(attempt["status"]), attempt["status"]))} ·
已到达：{_text(_PROCESS_LABELS.get(str(attempt["stage"]), attempt["stage"]))}</p>
{product}</section>{error}<section class="panel"><h2>固定输入与执行证据</h2>
<dl class="identity">{identity_text}</dl><details><summary>本次原始处理参数</summary>
<pre>{exact_parameters}</pre></details><details open><summary>已完成的质量证据</summary>
<pre>{quality}</pre></details></section><section class="panel"><h2>修正参数后重新处理</h2>
<p class="muted">{reprocess_note}</p>
<form id="reprocess-form" data-source-id="{_text(source["source_id"])}"{kind}>{fields}
<button type="submit">用这些参数重新处理原文</button>
<p id="reprocess-status" class="status" role="status"></p></form></section>"""


def _lineage_panel(lineage: dict[str, object]) -> str:
    references = _text(json.dumps(lineage["usages"], ensure_ascii=False, indent=2))
    return f"""<section class="panel"><h2>托管来源与处理链</h2>
<p class="muted">以下为当前可查询的来源、处理和引用；它们不改变本次研究或快照的固定身份。</p>
{_source_list(_rows(lineage["sources"]))}{_attempt_list(_rows(lineage["attempts"]))}
<details><summary>当前运行引用</summary><pre>{references}</pre></details></section>"""


def _dataset_label(data: dict[str, object]) -> str:
    return (
        f"{data['exchange']} · {data['symbol']} · {data['trading_day']} · "
        f"{data['session_open']} — {data['session_close']} · {data['bar_count']} bars"
    )


def _dataset_list(datasets: list[dict[str, object]]) -> str:
    if not datasets:
        return (
            '<div class="empty">还没有可研究的数据。导入并通过质量检查后，'
            "即使不运行研究，也会保存在这里。</div>"
        )
    rows = [
        f'<tr><td><a href="/datasets/{_text(data["snapshot_id"])}">'
        f"{_text(data['exchange'])} · {_text(data['symbol'])}</a></td>"
        f"<td>{_text(data['trading_day'])}</td>"
        f"<td>{_text(data['session_open'])}<br>{_text(data['session_close'])}</td>"
        f"<td>{_text(data['bar_count'])}</td>"
        f'<td><a href="/datasets/{_text(data["snapshot_id"])}">'
        "选用此数据</a></td></tr>"
        for data in datasets
    ]
    return _table(["实际合约", "交易日", "覆盖时段（UTC）", "Bars", ""], rows)


def _availability_notice(data: dict[str, object]) -> str:
    notices = {
        "FINAL_REVISED": (
            "最终修订数据 · 仅用于探索模拟",
            "信息时钟假设为每根 bar 完成时可见，并非历史上观测到的首次可得时间。"
            "不能据此证明当时能够做出相同决策。",
        ),
        "SOURCE_DECLARED": (
            "来源声明 · 未经独立验证",
            "available_at 依据由操作人声明，系统未独立验证它是否为历史首次可得时间。",
        ),
        "LOCAL_CAPTURE_RECONSTRUCTED": (
            "本机接收回调重建 · 非原影子决策重放",
            "按托管 JSON 中原本机收到时刻重建采样分钟，不是交易所发布时间、逐笔成交"
            "或生产行情证明；不回写原会话决策。",
        ),
        "SYNTHETIC": (
            "合成示例 · 非真实行情",
            "仅用于演示和工程验证，不用于评价真实市场中的策略表现。",
        ),
    }
    title, note = notices[str(data["availability_basis"])]
    return (
        f'<aside class="data-notice"><strong>{title}</strong><p>{note}</p>'
        f'<p class="muted">声明：{_text(data["availability_note"])}</p></aside>'
    )


def _dataset_page(data: dict[str, object]) -> str:
    return f"""<section class="intro report-intro"><a class="back" href="/">← 返回工作台</a>
<p class="eyebrow">ACCEPTED DATA</p><h1>{_text(data["symbol"])} · 数据详情</h1>
<p>{_text(data["trading_day"])} · {_text(data["bar_count"])} 个 bars · 接受时的固定证据</p>
<div class="actions"><a class="button"
href="/datasets/{_text(data["snapshot_id"])}">查看固定数据</a>
<a class="button secondary" href="/api/datasets/{_text(data["snapshot_id"])}"
download="dataset-{_text(data["snapshot_id"])}.json">下载数据说明</a></div></section>
{_availability_notice(data)}{_data_evidence(data)}"""


def _data_evidence(data: dict[str, object]) -> str:
    semantics, quality = _object(data["semantics"]), _object(data["quality"])
    minute = _object(quality["minute"])
    properties: tuple[tuple[str, object], ...] = (
        ("实际合约", f"{data['exchange']} / {data['product']} / {data['symbol']}"),
        ("交易日", data["trading_day"]),
        ("覆盖时段（UTC）", f"{data['session_open']} — {data['session_close']}"),
        ("时间语义", f"{semantics['interval_seconds']} 秒 / {semantics['timestamp_convention']}"),
        ("可得时间截点", semantics["available_at_cutoff"]),
        ("时区 / 币种", f"{semantics['timezone']} / {semantics['currency']}"),
        ("报价单位 / 成交量单位", f"{semantics['quantity_unit']} / {semantics['volume_unit']}"),
        ("最小价格变动 / 每手乘数", f"{semantics['price_tick']} / {semantics['multiplier']}"),
        ("价格调整方式", semantics["adjustment"]),
        ("来源出处", data["source_reference"]),
        ("数据快照", data["snapshot_id"]),
        ("数据摘要", data["content_hash"]),
        ("接受发布时间", data["published_at"]),
    )
    description = "".join(f"<dt>{label}</dt><dd>{_text(value)}</dd>" for label, value in properties)
    source_rows = [
        "<tr>"
        + "".join(
            f"<td>{_text(source[key])}</td>"
            for key in (
                "source_name",
                "received_at",
                "byte_count",
                "acquisition_use",
                "redistribution_policy",
                "retention_policy",
            )
        )
        + "</tr>"
        for source in _rows(data["sources"])
    ]
    import_rows = [
        "<tr>"
        + "".join(
            f"<td>{_text(item[key])}</td>"
            for key in (
                "outcome",
                "delivery_gate",
                "rows_read",
                "rows_accepted",
                "rows_rejected",
                "rows_inserted",
                "rows_duplicate_identical",
                "rows_conflicted",
            )
        )
        + "</tr>"
        for item in _rows(quality["imports"])
    ]
    quality_detail = "".join(
        f"<dt>{label}</dt><dd>{_text(minute[key])}</dd>"
        for label, key in (
            ("分钟质量结果", "outcome"),
            ("交付门禁", "delivery_gate"),
            ("应有观测数", "expected_observation_count"),
            ("实际观测数", "observed_count"),
            ("缺失观测数", "missing_observation_count"),
        )
    )
    identities = {
        "sources": data["sources"],
        "quality": data["quality"],
        "import_spec": data["import_spec"],
    }
    if "processing_provenance" in data:
        identities["processing_provenance"] = data["processing_provenance"]
    exact_evidence = escape(json.dumps(identities, ensure_ascii=False, sort_keys=True, indent=2))
    limitations = "".join(
        f"<li>{_text(note)}</li>" for note in cast(list[str], data["limitations"])
    )
    sources_table = _table(
        ["来源", "原始文件接收时间", "字节数", "使用依据", "再分发", "保留策略"], source_rows
    )
    return f"""<section class="panel"><h2>行情来源与数据含义</h2>
<dl class="identity">{description}</dl>{sources_table}</section>
<section class="panel"><h2>接受时的质量结果</h2><dl class="identity">{quality_detail}</dl>
{_table(["导入质量", "门禁", "读取", "接受", "拒绝", "新写入", "相同重复", "冲突"], import_rows)}
<details><summary>原始文件摘要、质量评估身份与导入声明</summary><pre>{exact_evidence}</pre></details></section>
<section class="panel"><h2>数据使用限制</h2><ul>{limitations}</ul></section>"""
