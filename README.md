# Manuphet

<img src="assets/manuphet.png" alt="Manuphet" width="96" align="right" />

企業向けの AI 需要供給予測 Web アプリケーションです。

需要実績（出荷・販売・受注など）から品目ごとの需要を XGBoost で予測し、
製品在庫・部品在庫があと何日もつかをブラウザで確認できます。
不足しそうな品目・部品はメールで通知します。

---

## 目次

1. [機能](#機能)
2. [クイックスタート](#クイックスタート)
3. [データの登録](#データの登録)
4. [ディレクトリ構成](#ディレクトリ構成)
5. [設定](#設定)
6. [CLI](#cli)
7. [Windows での常駐運用](#windows-での常駐運用)
8. [インストーラのビルド](#インストーラのビルド)
9. [テスト](#テスト)

詳しい手順は [docs/installation.md](docs/installation.md)（導入）と
[docs/user_guide.md](docs/user_guide.md)（画面の使い方）を参照してください。

---

## 機能

- **需要予測** — 品目ごとに月次または週次の XGBoost モデルを学習し、バックテストと今後の予測をグラフ表示
- **需要供給分析** — 予測需要と在庫・部品構成から、製品在庫・部品在庫の残日数と不足時期（1 か月以内 / 半年以内）を算出
- **データ入力** — 需要実績・在庫・部品構成を画面から手入力、または CSV で一括登録
- **MySQL 連携** — 既存の MySQL のテーブル・ビューから、必要な列と行だけを選んで取り込み。取込定義を保存して再同期できる
- **在庫不足通知** — 不足の見込みを SMTP でメール通知（初回 → 30 日後 → 180 日後）
- **自動再学習** — 30 日ごとに MySQL 取込定義を同期してから全品目を再学習
- FastAPI + 1 ファイルの Web UI（ダーク / ライト切替）

## クイックスタート

```powershell
cd project
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt

python run_web.py
# → http://localhost:8000 を開く
```

画面上部の「サンプルデータを投入」を押すと、架空の品目（SAMPLE-A / B / C）で動作を確認できます。
品目を選んで「学習」を押すと、予測グラフと需要供給が表示されます。

## データの登録

予測に使うデータはすべてローカルの SQLite データベース（既定: `%ProgramData%\Manuphet\data\manuphet.db`）に保存されます。

| データ | 内容 | 必須項目 |
|--------|------|----------|
| 需要実績 | 予測したい数量の実績（出荷・販売・受注など） | 日付, 品目コード, 数量（顧客は任意） |
| 在庫 | 製品・部品の現在庫（コード単位で上書き） | コード, 在庫数 |
| 部品構成 | 品目 1 台に使う部品と数量 | 品目コード, 部品コード（使用数は任意、既定 1） |

登録方法は 3 通りあります。

1. **手入力** — 「データ入力」タブのフォーム
2. **CSV** — 「データ入力 > CSV取込」。列名は英語（`record_date,item_code,quantity,customer` など）でも日本語（`日付,品目コード,数量,顧客` など）でも認識します
3. **MySQL** — 「MySQL連携」タブで接続 → テーブル選択 → 列の対応付け → 絞り込み条件 → プレビュー → 取込

MySQL 連携は参照（SELECT）のみです。テーブル名・列名は実際のスキーマに存在するものだけを受け付け、条件値はすべてバインドパラメータで渡します。

## ディレクトリ構成

```
project/
├── run_web.py             # 起動スクリプト（uvicorn）
├── manuphet_web.py        # FastAPI アプリ生成
├── config.py              # 集中設定（環境変数 / settings.json）
├── data_store.py          # ローカルデータストア（SQLite）
├── mysql_importer.py      # MySQL からの選択取込
├── model_handler.py       # 予測エンジン（XGBoost / Ridge）
├── email_notifier.py      # メール通知
├── cli.py                 # JSON を返す CLI（他システム連携用）
├── daily_train_all.py     # 一括学習バッチ
├── evaluate_models.py     # 予測精度の一括評価
├── setup_wizard.py        # 初回設定ウィザード（Tkinter）
├── api/
│   ├── service.py         # サービス層
│   └── routes.py          # API ルーター
├── model/                 # 特徴量・学習・保存などの部品
├── web/index.html         # Web UI
├── assets/                # アイコン（原画と生成物）
├── examples/sample_data/  # サンプル CSV
├── installer/             # PyInstaller / Inno Setup 定義
├── scripts/               # 自動起動登録・ビルド・アイコン/サンプル生成
├── docs/                  # 導入手順・使い方ガイド
└── tests/                 # pytest
```

## 設定

設定は次の優先順位で決まります。

1. 環境変数 `MANUPHET_*`（[.env.example](.env.example) 参照）
2. `project/settings.json`（開発用）
3. `%ProgramData%\Manuphet\data\config\settings.json`（セットアップウィザードが作成）
4. `config.py` の既定値

主な項目（[settings.example.json](settings.example.json) 参照）:

| settings.json | 環境変数 | 既定値 | 説明 |
|---------------|----------|--------|------|
| `host` / `port` | `MANUPHET_HOST` / `MANUPHET_PORT` | `0.0.0.0` / `8000` | 待受アドレス・ポート |
| `data_dir` | `MANUPHET_DATA_DIR` | `%ProgramData%\Manuphet\data` | データ保存先（DB・モデル・ログ・設定の基準） |
| `allow_web_train` | `MANUPHET_ALLOW_WEB_TRAIN` | `true` | 画面からの学習を許可 |
| `notify_auto` | `MANUPHET_NOTIFY_AUTO` | `true` | 在庫不足の定期チェック |
| `notify_interval_min` | `MANUPHET_NOTIFY_INTERVAL_MIN` | `360` | 定期チェックの間隔（分） |
| `auto_retrain_monthly` | `MANUPHET_AUTO_RETRAIN_MONTHLY` | `true` | 30 日ごとの自動再学習 |
| `search_iter` | `MANUPHET_SEARCH_ITER` | `100` | ハイパーパラメータ探索の試行回数 |
| `nav_links` | — | `[]` | ヘッダーに表示する外部リンク `[{"label": "...", "url": "https://..."}]` |

MySQL 接続情報は `config/mysql_config.json`、SMTP 設定は `config/smtp_config.json` に保存されます（どちらも画面から設定でき、パスワードは画面に返しません）。

## CLI

```powershell
python cli.py summary
python cli.py list
python cli.py predict --item ITEM-001 --months 6
python cli.py batch --items ITEM-001,ITEM-002 --months 3
python cli.py train --item ITEM-001
python cli.py train-all
python cli.py import-csv --target demand --file demand.csv --mode replace_source
python cli.py load-sample
python cli.py sync-mysql
```

結果は JSON で標準出力に出ます（失敗時は終了コード 1）。

## Windows での常駐運用

```powershell
# 管理者権限で実行（起動時に SYSTEM アカウントでウィンドウなし起動）
.\scripts\register_startup.ps1 -Port 8000

# 解除
.\scripts\unregister_startup.ps1
```

手動で起動・停止する場合は `start_manuphet.bat` / `stop_manuphet.bat` を使います。

## インストーラのビルド

[Inno Setup 6](https://jrsoftware.org/isinfo.php) と PyInstaller を用意して実行します。

```powershell
pip install -r requirements-dev.txt
.\scripts\build_installer.ps1
# → installer\Output\Manuphet_Setup_<version>.exe
```

アイコンは `assets/Manuphet_icon.png` から生成しています。差し替える場合は次を実行してください。

```powershell
python scripts\make_icons.py path\to\new_icon.png
```

## テスト

```powershell
pip install -r requirements-dev.txt
pytest tests -q
```

MySQL の取込処理は、SQLAlchemy の SQLite エンジンで代用してテストしています。

## ライセンス

[MIT License](LICENSE)
