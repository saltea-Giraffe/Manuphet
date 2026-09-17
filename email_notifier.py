"""
email_notifier.py  –  Manuphet メール通知（SMTP）

設定の優先順位:
  1. 環境変数 MANUPHET_SMTP_HOST / PORT / USER / PASS / FROM
  2. CONFIG_DIR/smtp_config.json（Web UI の「通知設定」タブから保存）
"""
from __future__ import annotations

import json
import os
import smtplib
from email.mime.text import MIMEText
from email.utils import formatdate


class EmailNotifier:
    def __init__(self):
        self.to_addrs: list[str] = []
        self.reload()

    def reload(self) -> None:
        """smtp_config.json の変更をインスタンスに反映する。"""
        cfg = self._load_smtp_config()
        self.smtp_server = os.getenv("MANUPHET_SMTP_HOST", cfg.get("smtp_server", "smtp.gmail.com"))
        self.smtp_port   = int(os.getenv("MANUPHET_SMTP_PORT", str(cfg.get("smtp_port", 587))))
        self.username    = os.getenv("MANUPHET_SMTP_USER", cfg.get("username", ""))
        self.password    = os.getenv("MANUPHET_SMTP_PASS", cfg.get("password", ""))
        self.from_addr   = os.getenv("MANUPHET_SMTP_FROM", cfg.get("from_addr", "")) or self.username

    @staticmethod
    def _load_smtp_config() -> dict:
        try:
            import config as _cfg
            p = _cfg.SMTP_CONFIG_JSON
            if p.exists():
                with p.open(encoding="utf-8") as f:
                    data = json.load(f)
                return data if isinstance(data, dict) else {}
        except Exception:
            pass
        return {}

    def set_to_addrs(self, addr_list) -> None:
        """送信先を設定する（文字列 1 件 / リストどちらも可。空文字と重複は除去）。"""
        if addr_list is None:
            self.to_addrs = []
            return
        addrs = [addr_list] if isinstance(addr_list, str) else list(addr_list)

        seen = set()
        cleaned: list[str] = []
        for a in addrs:
            a = str(a).strip()
            if not a or a in seen:
                continue
            seen.add(a)
            cleaned.append(a)
        self.to_addrs = cleaned

    def _validate(self) -> None:
        if not self.smtp_server:
            raise ValueError("SMTP サーバーが未設定です")
        if not self.username:
            raise ValueError("SMTP ユーザー名が未設定です")
        if not self.password:
            raise ValueError("SMTP パスワードが未設定です")
        if not self.from_addr:
            raise ValueError("送信元アドレスが未設定です")
        if not self.to_addrs:
            raise ValueError("送信先メールアドレスが登録されていません")

    def send_notification(self, subject: str, body: str, html_mode: bool = False) -> None:
        """メールを送信する（送信前に設定不備を検査する）。"""
        self._validate()

        msg = MIMEText(body, "html" if html_mode else "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = self.from_addr
        msg["To"] = ", ".join(self.to_addrs)
        msg["Date"] = formatdate(localtime=True)

        with smtplib.SMTP(self.smtp_server, self.smtp_port, timeout=30) as server:
            server.starttls()
            server.login(self.username, self.password)
            server.send_message(msg)
