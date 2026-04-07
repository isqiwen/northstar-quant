"""邮件发送模块。

这里负责把日报 / 周报 / 月报通过 SMTP 发送出去。
设计原则：
1. 报告先本地生成，确保有审计副本；
2. 再把 Markdown 转成 HTML 邮件发送；
3. 如启用 PDF 选项，则自动生成 PDF 并作为附件发送；
4. 发送失败不影响本地报告落盘。
"""

from __future__ import annotations

import mimetypes
import smtplib
from email.message import EmailMessage
from pathlib import Path

import markdown

from northstar_quant.config.settings import get_settings
from northstar_quant.logging_.logger import get_logger
from northstar_quant.reporting.pdf_renderer import markdown_to_pdf

logger = get_logger(__name__)


def _parse_recipients() -> list[str]:
    """解析收件人列表。

    环境变量里允许使用逗号分隔多个邮箱地址。
    """

    settings = get_settings()
    if not settings.report_recipients:
        return []
    return [x.strip() for x in settings.report_recipients.split(',') if x.strip()]


def _attach_file(msg: EmailMessage, file_path: str | Path) -> None:
    """把文件作为附件加入邮件。"""

    path = Path(file_path)
    mime_type, _ = mimetypes.guess_type(path.name)
    if mime_type:
        maintype, subtype = mime_type.split('/', 1)
    else:
        maintype, subtype = 'application', 'octet-stream'

    msg.add_attachment(
        path.read_bytes(),
        maintype=maintype,
        subtype=subtype,
        filename=path.name,
    )


def send_report_via_email(
    report_path: str | Path,
    subject: str | None = None,
    attach_pdf: bool | None = None,
) -> dict:
    """发送报告邮件。

    参数：
    - report_path: 已经生成好的 Markdown 报告路径
    - subject: 可选邮件主题；不传时自动生成
    - attach_pdf: 是否附带 PDF；为空时读取系统配置

    返回：
    - dict，方便 CLI 和调度器直接打印执行结果
    """

    settings = get_settings()
    recipients = _parse_recipients()
    path = Path(report_path)
    email_logger = logger.bind(command="report.email", report_path=str(path))

    if attach_pdf is None:
        attach_pdf = settings.report_email_attach_pdf

    if not recipients:
        email_logger.warning("未配置收件人，跳过邮件发送")
        return {
            'sent': False,
            'reason': '未配置 NORTHSTAR_REPORT_RECIPIENTS，已跳过邮件发送。',
            'report_path': str(path),
        }

    required = [settings.smtp_host, settings.smtp_sender]
    if not all(required):
        email_logger.warning("SMTP 参数不完整，跳过邮件发送")
        return {
            'sent': False,
            'reason': 'SMTP 参数不完整，至少需要 smtp_host 和 smtp_sender。',
            'report_path': str(path),
            'recipients': recipients,
        }

    markdown_text = path.read_text(encoding='utf-8')
    html_body = markdown.markdown(markdown_text, extensions=['tables', 'fenced_code'])

    pdf_path = None
    if attach_pdf:
        pdf_path = markdown_to_pdf(path)
        email_logger.info("已生成 PDF 附件，pdf_path=%s", pdf_path)

    msg = EmailMessage()
    msg['From'] = settings.smtp_sender
    msg['To'] = ', '.join(recipients)
    msg['Subject'] = subject or f"{settings.report_email_subject_prefix} - {path.stem}"
    msg.set_content(markdown_text)
    msg.add_alternative(html_body, subtype='html')

    # 默认同时附上 Markdown 和 PDF，兼顾可读性与可审计性。
    _attach_file(msg, path)
    if pdf_path:
        _attach_file(msg, pdf_path)

    if settings.smtp_use_ssl:
        with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port) as server:
            if settings.smtp_username:
                server.login(settings.smtp_username, settings.smtp_password or '')
            server.send_message(msg)
    else:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
            server.ehlo()
            try:
                server.starttls()
                server.ehlo()
            except smtplib.SMTPException:
                # 某些内网或本地邮件服务不支持 STARTTLS，允许降级。
                pass
            if settings.smtp_username:
                server.login(settings.smtp_username, settings.smtp_password or '')
            server.send_message(msg)

    email_logger.info("报告邮件发送完成，recipient_count=%s", len(recipients))

    return {
        'sent': True,
        'report_path': str(path),
        'pdf_path': pdf_path,
        'recipients': recipients,
        'subject': msg['Subject'],
        'attachments': [x for x in [str(path), pdf_path] if x],
    }
