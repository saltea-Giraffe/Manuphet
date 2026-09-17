# Manuphet 導入手順

Windows PC（サーバー役）に Manuphet を導入する手順です。
導入後は、同じネットワーク上の PC からブラウザで `http://<サーバーのアドレス>:8000/` を開いて利用します。

---

## 前提条件

| 項目 | 要件 |
|------|------|
| OS | Windows 10 / 11（64bit） |
| Python | 3.10 以上（インストーラ版は不要） |
| MySQL | 任意。連携する場合はサーバーへ接続できること（参照権限のみで可） |
| メール | 任意。通知する場合は SMTP サーバー（Gmail など）のアカウント |

---

## 方法 A: インストーラを使う（推奨）

1. `Manuphet_Setup_x.x.x.exe` を管理者として実行する
2. インストール先を選ぶ（既定: `C:\Program Files\Manuphet`）
3. 「Windows 起動時に自動起動する」「セットアップウィザードを起動する」にチェックを入れる
4. セットアップウィザードで次を設定する
   - Web サーバーのポート（既定 8000）とデータ保存先
   - MySQL 接続（使う場合のみ）
   - 自動起動の登録
   - メール通知の SMTP（使う場合のみ）
5. PC を再起動するか、タスクスケジューラで `Manuphet_Web` を手動実行する
6. ブラウザで `http://localhost:8000/` を開く

---

## 方法 B: Python で動かす

### 1. ファイル配置と仮想環境

```powershell
cd C:\Apps\Manuphet          # project/ の中身を配置したフォルダ
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2. 設定（任意）

既定のままでも動きます。変更する場合は次のどちらかを行います。

- `python setup_wizard.py` を実行する（`%ProgramData%\Manuphet\data\config\settings.json` が作られます）
- `settings.example.json` を `settings.json` にコピーして編集する

### 3. 起動確認

```powershell
python run_web.py
# ブラウザで http://localhost:8000 を開く
```

「サンプルデータを投入」ボタンで動作を確認できます。確認後は「データ入力 > CSV取込 > データの削除」でサンプルを消せます。

### 4. 自動起動の登録

```powershell
# 管理者権限で実行
.\scripts\register_startup.ps1 -InstallDir "C:\Apps\Manuphet" -Port 8000

# 今すぐ起動
Start-ScheduledTask -TaskName "Manuphet_Web"
```

---

## データの保存場所

| 内容 | 既定の場所 |
|------|-----------|
| 設定（settings / MySQL / SMTP / 送信先など） | `%ProgramData%\Manuphet\data\config\` |
| 登録データ（SQLite） | `%ProgramData%\Manuphet\data\manuphet.db` |
| 学習済みモデル | `%ProgramData%\Manuphet\data\models\` |
| ログ | `%ProgramData%\Manuphet\data\logs\` |

バックアップは `%ProgramData%\Manuphet\data` フォルダごと取得してください。
`config` フォルダには MySQL・SMTP のパスワードが平文で保存されるため、フォルダのアクセス権に注意してください。

---

## MySQL 連携のポイント

- Manuphet は MySQL に対して参照（SELECT）しか行いません。読み取り専用ユーザーを作成して使うことをおすすめします
  ```sql
  CREATE USER 'manuphet_ro'@'%' IDENTIFIED BY '********';
  GRANT SELECT ON your_database.* TO 'manuphet_ro'@'%';
  ```
- 取り込みたい情報が複数のテーブルに分かれている場合は、MySQL 側でビューを作成すると 1 つの取込定義で扱えます
- 取込定義を保存しておくと、「すべて同期」ボタン・`python cli.py sync-mysql`・月次自動再学習の前に最新データへ同期されます

---

## 定期バッチ（任意）

自動再学習（30 日ごと）とは別に、決まった時刻に学習したい場合はタスクスケジューラで次を実行します。

```powershell
C:\Apps\Manuphet\.venv\Scripts\python.exe C:\Apps\Manuphet\daily_train_all.py
```

---

## トラブルシューティング

### ポートが使用中

```
OSError: [Errno 10048] error while binding
```

→ `settings.json` の `port` を変更するか、`netstat -ano | findstr :8000` で使用中のプロセスを確認してください。

### 「モデルがありません」と表示される

→ 品目を選んで「学習」を押すか、「学習管理」タブで一括学習を実行してください。

### MySQL に接続できない

- ホスト・ポート・データベース名・ユーザー・パスワードを確認する
- サーバー側のファイアウォールと、ユーザーの接続元ホスト制限（`'user'@'host'`）を確認する
- 認証方式の問題が出る場合は、ドライバを「PyMySQL」に切り替えて試す

### 自動起動しない

→ タスクスケジューラの `Manuphet_Web` の履歴と、`%ProgramData%\Manuphet\data\logs\service.log` を確認してください。
