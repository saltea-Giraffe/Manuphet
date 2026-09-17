"""
setup_wizard.py  –  Manuphet セットアップウィザード（Tkinter GUI）

初回インストール後またはインストーラから呼び出す。
設定内容を %ProgramData%\\Manuphet\\data\\config\\settings.json に書き込む。
必要に応じてタスクスケジューラへの登録も行う。

使い方:
    python setup_wizard.py
"""
from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Any

# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------
APP_NAME      = "Manuphet"
APP_TITLE     = f"{APP_NAME} セットアップウィザード"
TASK_NAME     = "Manuphet_Web"
WEB_EXE_NAME  = "Manuphet_Web.exe"
PROGRAMDATA   = Path(os.environ.get("PROGRAMDATA", "C:/ProgramData"))
APP_DATA_DIR  = PROGRAMDATA / APP_NAME / "data"
SETTINGS_DIR  = APP_DATA_DIR / "config"
SETTINGS_FILE = SETTINGS_DIR / "settings.json"

# インストールディレクトリ / 同梱リソースの解決
# PyInstaller onefile 実行時: __file__ は一時展開フォルダを指すため、
# インストール先は sys.executable、同梱リソースは sys._MEIPASS から解決する。
if getattr(sys, "frozen", False):
    INSTALL_DIR = Path(sys.executable).parent
    RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", INSTALL_DIR))
else:
    INSTALL_DIR = Path(os.environ.get("MANUPHET_INSTALL_DIR", str(Path(__file__).resolve().parent)))
    RESOURCE_DIR = Path(__file__).resolve().parent
ICON_FILE = RESOURCE_DIR / "assets" / "manuphet.ico"

COLOR_BG     = "#0B1030"
COLOR_FG     = "#7FE8FF"
COLOR_PANEL  = "#18204A"
COLOR_INPUT  = "#0E1438"
COLOR_NOTE   = "#E5CAFF"
COLOR_BUTTON = "#1565C0"
COLOR_OK     = "#388E3C"


def _is_admin() -> bool:
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def _load_existing(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _default_python_exe() -> str:
    """自動起動に使う実行ファイルパスを返す（バンドル版は Manuphet_Web.exe を優先）。"""
    if getattr(sys, "frozen", False):
        web_exe = INSTALL_DIR / WEB_EXE_NAME
        if web_exe.exists():
            return str(web_exe)

    saved = _load_existing(SETTINGS_FILE).get("python_exe", "")
    if saved and Path(saved).exists():
        return saved
    for rel in (".venv/Scripts/pythonw.exe", ".venv/Scripts/python.exe",
                "venv/Scripts/pythonw.exe", "venv/Scripts/python.exe"):
        c = INSTALL_DIR / rel
        if c.exists():
            return str(c)
    exe = Path(sys.executable)
    pw = exe.parent / "pythonw.exe"
    return str(pw if pw.exists() else exe)


# ---------------------------------------------------------------------------
# ウィザード
# ---------------------------------------------------------------------------

class SetupWizard(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("720x620")
        self.configure(bg=COLOR_BG)
        self.resizable(False, False)
        if ICON_FILE.exists():
            try:
                self.iconbitmap(default=str(ICON_FILE))
            except tk.TclError:
                pass

        current = _load_existing(SETTINGS_FILE)
        mysql = _load_existing(SETTINGS_DIR / "mysql_config.json")
        smtp = _load_existing(SETTINGS_DIR / "smtp_config.json")

        # 入力変数（既存設定があれば初期値に使う）
        self.v_port           = tk.IntVar(value=int(current.get("port", 8000)))
        self.v_data_dir       = tk.StringVar(value=current.get("data_dir", str(APP_DATA_DIR)))
        self.v_models_dir     = tk.StringVar(value=current.get("models_dir", str(APP_DATA_DIR / "models")))
        self.v_python_exe     = tk.StringVar(value=_default_python_exe())
        self.v_mysql_skip     = tk.BooleanVar(value=not mysql.get("host"))
        self.v_mysql_host     = tk.StringVar(value=mysql.get("host", "127.0.0.1"))
        self.v_mysql_port     = tk.IntVar(value=int(mysql.get("port", 3306)))
        self.v_mysql_user     = tk.StringVar(value=mysql.get("user", ""))
        self.v_mysql_password = tk.StringVar(value="")
        self.v_mysql_database = tk.StringVar(value=mysql.get("database", ""))
        self.v_auto_start     = tk.BooleanVar(value=True)
        self.v_smtp_skip      = tk.BooleanVar(value=not smtp.get("username"))
        self.v_smtp_server    = tk.StringVar(value=smtp.get("smtp_server", "smtp.gmail.com"))
        self.v_smtp_port      = tk.IntVar(value=int(smtp.get("smtp_port", 587)))
        self.v_smtp_user      = tk.StringVar(value=smtp.get("username", ""))
        self.v_smtp_from      = tk.StringVar(value=smtp.get("from_addr", ""))
        self.v_smtp_pass      = tk.StringVar(value="")
        self._mysql_password_saved = bool(mysql.get("password"))
        self._smtp_password_saved = bool(smtp.get("password"))

        self._pages: list[tk.Frame] = []
        self._mysql_widgets: list[tk.Widget] = []
        self._smtp_widgets: list[tk.Widget] = []
        self._current = 0

        self._build_ui()
        self._on_mysql_skip_change()
        self._on_smtp_skip_change()
        self._show_page(0)

    # ------------------------------------------------------------------
    # UI 構築
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        hdr = tk.Frame(self, bg=COLOR_BG, pady=10)
        hdr.pack(fill="x")
        tk.Label(hdr, text=APP_TITLE, bg=COLOR_BG, fg=COLOR_FG,
                 font=("Segoe UI", 14, "bold")).pack()

        self._content = tk.Frame(self, bg=COLOR_BG, padx=20, pady=10)
        self._content.pack(fill="both", expand=True)

        self._pages = [
            self._page_server(),
            self._page_mysql(),
            self._page_startup(),
            self._page_smtp(),
            self._page_confirm(),
        ]
        for p in self._pages:
            p.place(in_=self._content, x=0, y=0, relwidth=1, relheight=1)

        nav = tk.Frame(self, bg=COLOR_BG, pady=8)
        nav.pack(fill="x", side="bottom")
        self._btn_back = tk.Button(nav, text="< 戻る", width=10, bg=COLOR_PANEL, fg=COLOR_FG,
                                   command=self._prev_page)
        self._btn_back.pack(side="left", padx=20)
        self._btn_next = tk.Button(nav, text="次へ >", width=10, bg=COLOR_BUTTON, fg="white",
                                   command=self._next_page)
        self._btn_next.pack(side="right", padx=20)

    def _title(self, parent: tk.Widget, text: str) -> None:
        tk.Label(parent, text=text, bg=COLOR_BG, fg=COLOR_FG,
                 font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(0, 10))

    def _lf(self, parent: tk.Widget, text: str) -> tk.LabelFrame:
        lf = tk.LabelFrame(parent, text=text, bg=COLOR_PANEL, fg=COLOR_FG,
                           font=("Segoe UI", 10, "bold"), relief="groove", padx=10, pady=8)
        lf.pack(fill="x", pady=6)
        return lf

    def _note(self, parent: tk.Widget, text: str) -> None:
        tk.Label(parent, text=text, bg=COLOR_PANEL, fg=COLOR_NOTE, justify="left").pack(anchor="w", pady=(4, 0))

    def _entry(self, parent: tk.Widget, textvariable: tk.Variable, **kw) -> tk.Entry:
        return tk.Entry(parent, textvariable=textvariable, bg=COLOR_INPUT, fg=COLOR_FG,
                        insertbackground=COLOR_FG, disabledbackground=COLOR_PANEL,
                        relief="flat", **kw)

    def _row(self, parent: tk.Widget, label: str, var: tk.Variable, browse=None, **kw) -> tk.Entry:
        f = tk.Frame(parent, bg=COLOR_PANEL)
        f.pack(fill="x", pady=2)
        tk.Label(f, text=label, width=22, anchor="w", bg=COLOR_PANEL, fg=COLOR_FG).pack(side="left")
        w = self._entry(f, var, **kw)
        w.pack(side="left", fill="x", expand=True, padx=4)
        if browse is not None:
            tk.Button(f, text="参照", bg=COLOR_PANEL, fg=COLOR_FG, command=browse).pack(side="left")
        return w

    def _check(self, parent: tk.Widget, text: str, var: tk.BooleanVar, command=None) -> None:
        tk.Checkbutton(parent, text=text, variable=var, command=command,
                       bg=COLOR_PANEL, fg=COLOR_FG, selectcolor=COLOR_BG,
                       activebackground=COLOR_PANEL, activeforeground=COLOR_FG).pack(anchor="w")

    def _browse_dir(self, var: tk.StringVar) -> None:
        d = filedialog.askdirectory(initialdir=var.get() or "/")
        if d:
            var.set(d)

    def _browse_exe(self, var: tk.StringVar) -> None:
        f = filedialog.askopenfilename(
            initialdir=str(Path(var.get()).parent) if var.get() else "/",
            filetypes=(("実行ファイル", "*.exe"), ("All", "*")),
        )
        if f:
            var.set(f)

    # ------------------------------------------------------------------
    # ページ定義
    # ------------------------------------------------------------------
    def _page(self) -> tk.Frame:
        return tk.Frame(self._content, bg=COLOR_BG)

    def _page_server(self) -> tk.Frame:
        p = self._page()
        self._title(p, "ステップ 1 / 4  –  サーバーと保存先")

        lf = self._lf(p, "Web サーバー")
        self._row(lf, "待受ポート", self.v_port)
        self._note(lf, "ブラウザで http://<このPCのアドレス>:<ポート>/ を開いて利用します。")

        lf2 = self._lf(p, "データ保存先（需要実績・在庫・学習済みモデル）")
        self._row(lf2, "データディレクトリ", self.v_data_dir, browse=lambda: self._browse_dir(self.v_data_dir))
        self._row(lf2, "モデル保存先", self.v_models_dir, browse=lambda: self._browse_dir(self.v_models_dir))

        lf3 = self._lf(p, "自動起動に使う実行ファイル")
        self._row(lf3, "実行ファイル", self.v_python_exe, browse=lambda: self._browse_exe(self.v_python_exe))
        self._note(lf3, f"インストーラ版は {WEB_EXE_NAME}、Python 版は pythonw.exe を選びます（ウィンドウ非表示）。")
        return p

    def _page_mysql(self) -> tk.Frame:
        p = self._page()
        self._title(p, "ステップ 2 / 4  –  MySQL 接続（任意）")

        lf_skip = self._lf(p, "MySQL 連携")
        self._check(lf_skip, "MySQL を使わない（データは画面または CSV から登録する）",
                    self.v_mysql_skip, command=self._on_mysql_skip_change)

        lf = self._lf(p, "接続設定（Web 画面の「MySQL連携」タブからも変更できます）")
        self._mysql_widgets = [
            self._row(lf, "ホスト", self.v_mysql_host),
            self._row(lf, "ポート", self.v_mysql_port),
            self._row(lf, "データベース", self.v_mysql_database),
            self._row(lf, "ユーザー", self.v_mysql_user),
            self._row(lf, "パスワード", self.v_mysql_password, show="*"),
        ]
        note = "取り込むテーブル・列・条件は、Web 画面の「MySQL連携」タブで選択します。\n参照（SELECT）のみ行うため、読み取り専用ユーザーの利用をおすすめします。"
        if self._mysql_password_saved:
            note += "\nパスワードは保存済みです（変更する場合のみ入力してください）。"
        self._note(lf, note)
        return p

    def _page_startup(self) -> tk.Frame:
        p = self._page()
        self._title(p, "ステップ 3 / 4  –  自動起動")
        lf = self._lf(p, "Windows 起動時に自動起動する")
        self._check(lf, "タスクスケジューラに自動起動タスクを登録する（推奨）", self.v_auto_start)
        self._note(lf,
                   "・SYSTEM アカウントで実行されるため、ログイン不要で起動します。\n"
                   "・コンソールウィンドウは表示されません。\n"
                   f"・ログは {APP_DATA_DIR}\\logs\\service.log に出力されます。\n"
                   "・登録には管理者権限が必要です（必要な場合は昇格ダイアログが表示されます）。")
        return p

    def _page_smtp(self) -> tk.Frame:
        p = self._page()
        self._title(p, "ステップ 4 / 4  –  メール通知（任意）")

        lf_skip = self._lf(p, "メール通知")
        self._check(lf_skip, "メール通知を使わない（あとから Web 画面で設定できます）",
                    self.v_smtp_skip, command=self._on_smtp_skip_change)

        lf = self._lf(p, "SMTP 設定")
        self._smtp_widgets = [
            self._row(lf, "SMTP サーバー", self.v_smtp_server),
            self._row(lf, "SMTP ポート", self.v_smtp_port),
            self._row(lf, "ユーザー名（メールアドレス）", self.v_smtp_user),
            self._row(lf, "送信元表示アドレス（省略可）", self.v_smtp_from),
            self._row(lf, "パスワード", self.v_smtp_pass, show="*"),
        ]
        note = "Gmail の場合は、Google アカウントのセキュリティ設定で発行した\n16 文字のアプリパスワードを入力してください。"
        if self._smtp_password_saved:
            note += "\nパスワードは保存済みです（変更する場合のみ入力してください）。"
        self._note(lf, note)
        return p

    @staticmethod
    def _set_state(widgets: list[tk.Widget], disabled: bool) -> None:
        for w in widgets:
            try:
                w.configure(state="disabled" if disabled else "normal")
            except tk.TclError:
                pass

    def _on_mysql_skip_change(self) -> None:
        self._set_state(self._mysql_widgets, self.v_mysql_skip.get())

    def _on_smtp_skip_change(self) -> None:
        self._set_state(self._smtp_widgets, self.v_smtp_skip.get())

    def _page_confirm(self) -> tk.Frame:
        p = self._page()
        self._title(p, "設定確認")
        self._confirm_text = tk.Text(p, height=18, bg=COLOR_PANEL, fg=COLOR_FG, relief="flat", state="disabled")
        self._confirm_text.pack(fill="both", expand=True)
        return p

    def _update_confirm_text(self) -> None:
        lines = [f"{SETTINGS_FILE}\n", json.dumps(self._build_settings_dict(), ensure_ascii=False, indent=2)]
        if self.v_mysql_skip.get():
            lines.append("\n\nMySQL 連携: 設定しない")
        else:
            mysql = dict(self._build_mysql_dict())
            mysql["password"] = "****" if (mysql["password"] or self._mysql_password_saved) else "(未設定)"
            lines.append("\n\nmysql_config.json:\n" + json.dumps(mysql, ensure_ascii=False, indent=2))
        if self.v_smtp_skip.get():
            lines.append("\n\nメール通知: 設定しない")
        else:
            smtp = dict(self._build_smtp_dict())
            smtp["password"] = "****" if (smtp["password"] or self._smtp_password_saved) else "(未設定)"
            lines.append("\n\nsmtp_config.json:\n" + json.dumps(smtp, ensure_ascii=False, indent=2))
        lines.append("\n\n自動起動: " + ("登録する" if self.v_auto_start.get() else "登録しない"))
        self._confirm_text.configure(state="normal")
        self._confirm_text.delete("1.0", "end")
        self._confirm_text.insert("end", "".join(lines))
        self._confirm_text.configure(state="disabled")

    # ------------------------------------------------------------------
    # ナビゲーション
    # ------------------------------------------------------------------
    def _show_page(self, idx: int) -> None:
        if idx == len(self._pages) - 1:
            self._update_confirm_text()
            self._btn_next.configure(text="完了・保存", bg=COLOR_OK)
        else:
            self._btn_next.configure(text="次へ >", bg=COLOR_BUTTON)
        self._btn_back.configure(state="normal" if idx > 0 else "disabled")
        self._pages[idx].lift()
        self._current = idx

    def _next_page(self) -> None:
        if self._current == len(self._pages) - 1:
            self._finish()
        else:
            try:
                self.v_port.get()
                self.v_mysql_port.get()
                self.v_smtp_port.get()
            except tk.TclError:
                messagebox.showerror("入力エラー", "ポート番号は数字で入力してください。")
                return
            self._show_page(self._current + 1)

    def _prev_page(self) -> None:
        if self._current > 0:
            self._show_page(self._current - 1)

    # ------------------------------------------------------------------
    # 設定生成 / 保存
    # ------------------------------------------------------------------
    def _build_settings_dict(self) -> dict[str, Any]:
        s = _load_existing(SETTINGS_FILE)
        s.update({
            "port":       self.v_port.get(),
            "data_dir":   self.v_data_dir.get().strip(),
            "models_dir": self.v_models_dir.get().strip(),
            # 設定ファイル類は常にこのウィザードが書き込む場所を使う
            "config_dir": str(SETTINGS_DIR),
            "python_exe": self.v_python_exe.get().strip(),
        })
        return s

    def _build_mysql_dict(self) -> dict[str, Any]:
        return {
            "host":     self.v_mysql_host.get().strip(),
            "port":     self.v_mysql_port.get(),
            "user":     self.v_mysql_user.get().strip(),
            "password": self.v_mysql_password.get(),
            "database": self.v_mysql_database.get().strip(),
            "driver":   "mysqlconnector",
        }

    def _build_smtp_dict(self) -> dict[str, Any]:
        return {
            "smtp_server": self.v_smtp_server.get().strip(),
            "smtp_port":   self.v_smtp_port.get(),
            "username":    self.v_smtp_user.get().strip(),
            "from_addr":   self.v_smtp_from.get().strip() or self.v_smtp_user.get().strip(),
            "password":    self.v_smtp_pass.get(),
        }

    @staticmethod
    def _write_json(path: Path, data: dict[str, Any], keep_password: bool) -> None:
        if keep_password and not data.get("password"):
            old = _load_existing(path).get("password")
            if old:
                data["password"] = old
        with path.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _finish(self) -> None:
        try:
            SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
            with SETTINGS_FILE.open("w", encoding="utf-8") as f:
                json.dump(self._build_settings_dict(), f, ensure_ascii=False, indent=2)

            if not self.v_mysql_skip.get():
                self._write_json(SETTINGS_DIR / "mysql_config.json", self._build_mysql_dict(), keep_password=True)
            if not self.v_smtp_skip.get():
                self._write_json(SETTINGS_DIR / "smtp_config.json", self._build_smtp_dict(), keep_password=True)

            if self.v_auto_start.get():
                self._register_task()

            messagebox.showinfo(
                "完了",
                f"設定を保存しました:\n{SETTINGS_FILE}\n\n"
                f"Windows 再起動後、{APP_NAME} が自動起動します。\n"
                "今すぐ起動する場合はタスクスケジューラから手動で開始してください。",
            )
            self.destroy()

        except PermissionError:
            messagebox.showerror("エラー", f"{SETTINGS_FILE} への書き込み権限がありません。\n管理者として実行してください。")
        except Exception as e:
            messagebox.showerror("エラー", f"保存中にエラーが発生しました:\n{e}")

    def _register_task(self) -> None:
        """register_startup.ps1 を管理者権限で実行する（必要なら UAC 昇格）。"""
        script = INSTALL_DIR / "scripts" / "register_startup.ps1"
        if not script.exists():
            messagebox.showwarning("警告", f"register_startup.ps1 が見つかりません:\n{script}")
            return

        exe = self.v_python_exe.get().strip()
        args = ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
                "-Port", str(self.v_port.get()), "-InstallDir", str(INSTALL_DIR),
                "-PythonExe", exe, "-LogDir", str(Path(self.v_data_dir.get().strip()) / "logs")]

        if _is_admin():
            result = subprocess.run(["powershell", *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE)

            def _decode(b: bytes) -> str:
                # PowerShell の出力は UTF-16LE / CP932 / UTF-8 のいずれかになりうる
                for enc in ("utf-16", "cp932", "utf-8"):
                    try:
                        return b.decode(enc)
                    except Exception:
                        pass
                return b.decode("cp932", errors="replace")

            if result.returncode != 0:
                err = (_decode(result.stderr) or _decode(result.stdout) or "不明なエラー")[:800]
                messagebox.showwarning("自動起動登録の警告", "タスクスケジューラへの登録に失敗しました。\n\n" + err)
            else:
                messagebox.showinfo("自動起動", "タスクスケジューラへの登録が完了しました。")
        else:
            ps_args = subprocess.list2cmdline(args)
            ret = ctypes.windll.shell32.ShellExecuteW(None, "runas", "powershell.exe", ps_args, None, 1)
            if ret <= 32:
                messagebox.showwarning(
                    "自動起動登録の警告",
                    "管理者昇格がキャンセルされたか失敗しました。\n"
                    "後から scripts\\register_startup.ps1 を管理者として実行してください。",
                )
            else:
                messagebox.showinfo("自動起動", "管理者権限でタスク登録を実行しています。\n"
                                               "完了後に Windows を再起動してください。")


def main() -> None:
    SetupWizard().mainloop()


if __name__ == "__main__":
    main()
