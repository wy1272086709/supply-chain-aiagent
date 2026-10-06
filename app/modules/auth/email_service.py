"""SMTP 邮件 — aiosmtplib，模板中文逐字保留（对应 email.service.ts）。"""

from __future__ import annotations

import logging
from email.message import EmailMessage
from email.utils import parseaddr

import aiosmtplib

from app.core.config import get_settings

logger = logging.getLogger(__name__)


def _extract_from(raw_from: str | None) -> tuple[str, str]:
    """'Knowledge Hub <a@b.com>' → (addr, 显示名)。"""
    if not raw_from:
        return "", ""
    name, addr = parseaddr(raw_from)
    display = name or (addr.split("@")[0] if addr else "")
    return addr, display


async def _send(to: str, subject: str, text: str, html: str) -> None:
    cfg = get_settings()
    from_addr, from_display = _extract_from(cfg.mail_from)
    if not from_addr:
        from_addr = cfg.mail_user
        from_display = "Knowledge Hub"

    msg = EmailMessage()
    msg["From"] = f"{from_display} <{from_addr}>" if from_display else from_addr
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")

    await aiosmtplib.send(
        msg,
        hostname=cfg.mail_host,
        port=cfg.mail_port,
        username=cfg.mail_user or None,
        password=cfg.mail_pass or None,
        start_tls=cfg.mail_secure != "true" and cfg.mail_port == 587,
        use_tls=cfg.mail_secure == "true",
    )


async def send_activation_email(email: str, username: str, token: str) -> None:
    cfg = get_settings()
    base_url = cfg.app_public_url or "http://localhost:3000"
    link = f"{base_url}/auth/verify-email?token={token}"
    await _send(
        email,
        "激活您的知识库账户",
        f"您好 {username}，请点击以下链接激活账户（24 小时内有效）：\n{link}",
        f"""
        <p>您好 <strong>{username}</strong>，</p>
        <p>欢迎注册知识库，请点击下方链接激活账户（24 小时内有效）：</p>
        <p><a href="{link}">{link}</a></p>
        <p>如非本人操作，请忽略此邮件。</p>
      """,
    )


async def send_reset_code_email(email: str, username: str, code: str) -> None:
    await _send(
        email,
        "密码重置验证码",
        f"您好 {username}，您的密码重置验证码是：{code}，10 分钟内有效，请勿泄露。",
        f"""
        <p>您好 <strong>{username}</strong>，</p>
        <p>您正在重置密码，验证码为：</p>
        <p style="font-size:24px;font-weight:bold;letter-spacing:4px;">{code}</p>
        <p>验证码 10 分钟内有效，请勿泄露给他人。</p>
        <p>如非本人操作，请忽略此邮件。</p>
      """,
    )
