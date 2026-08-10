# SEPA スクリーナー自動実行 (Cloud Run Jobs)

Colabで毎日手動実行していた株式スクリーニングを、Google Cloud上で完全自動化したもの。
人手を介さずに、スケジュール通りスプレッドシートが更新される。

**スコアリングのロジック・フィルタ条件・閾値はColab版から一切変更していない。**
変えたのは実行環境(Colab → Cloud Run Jobs)だけ。

---

## 1. 全体像

| ジョブ名 | 中身 | 既定スケジュール(UTC) | 出力先スプレッドシート |
|---|---|---|---|
| `minervini-screener` | Finviz一次選別 → トレンドテンプレート8条件 + VCP + ファンダスコア + 地合い判定 | `30 23 * * 1-5`(平日 / JST翌 08:30) | ミネルヴィニ銘柄スクリーナー →「抽出結果」 |
| `momentum-screener` | セクターETF・個別銘柄のモメンタムを現在/1週間前/1ヶ月前の3時点で算出 | `45 23 * * 1-5`(平日 / JST翌 08:45) | モメンタムスクリーナー → 3タブ |
| `thesis-generator` | ショートリストに対しClaude APIで強気/弱気/ベースシナリオ+反証可能なテーゼを生成 | `0 0 * * 2,5`(火・金 / JST 09:00) | ミネルヴィニ銘柄スクリーナー →「投資テーゼ」 |

```
Cloud Scheduler ──(OIDC/OAuth)──▶ Cloud Run Jobs ──▶ Google Sheets
                                        │
                                        └── Secret Manager (ANTHROPIC_API_KEY)
```

**サービスアカウントのJSON鍵は一切使わない。** Cloud Run Jobs にサービスアカウントを
アタッチし、コード側は `google.auth.default()` でその認証情報を受け取る。

### なぜ 23:30 UTC なのか

米国市場の引けは、夏時間(EDT)なら 20:00 UTC、冬時間(EST)なら 21:00 UTC。
両方の期間で確実に引け後になるよう 23:30 UTC を既定にしている
(21:30 UTC だと冬時間の期間はザラ場中のデータを拾ってしまう)。
変更したい場合は `deploy/config.sh` の `MINERVINI_SCHEDULE` 等を編集する。

---

## 2. ファイル構成

```
.
├── common.py                    共通処理: 認証 / リトライ / 実行ステータス記録
├── requirements.txt             ルート(下2つを取り込むだけ)
├── Procfile                     buildpacks用。実行時は --command/--args で上書きされる
├── screeners/
│   ├── minervini_screener.py    ミネルヴィニ・スクリーナー
│   ├── momentum_screener.py     モメンタム・スクリーナー
│   └── requirements.txt
├── research/
│   ├── generate_thesis.py       投資テーゼ生成(ショートリスト接続済み)
│   └── requirements.txt
└── deploy/
    ├── config.sh.example        設定のひな形(コピーして config.sh を作る)
    ├── deploy.sh                Cloud Run Jobs へのデプロイ
    ├── schedule.sh              Cloud Scheduler の設定
    └── alert.sh                 失敗時メール通知の設定(任意)
```

3ジョブとも**同じイメージ**(リポジトリ全体)から起動し、起動コマンドだけが違う。
`common.py` を3ジョブで共有するため、この構成にしている。

---

## 3. 初回セットアップ

### 3-1. 前提の確認

```bash
gcloud auth login
gcloud config set project <PROJECT_ID>

gcloud services enable \
  run.googleapis.com cloudscheduler.googleapis.com \
  secretmanager.googleapis.com sheets.googleapis.com \
  drive.googleapis.com cloudbuild.googleapis.com
```

### 3-2. 実行用サービスアカウント(鍵は発行しない)

```bash
PROJECT_ID="$(gcloud config get-value project)"

gcloud iam service-accounts create screener-runner \
  --display-name="Screener Job Runner"

# Secret Manager の読み取り(thesis-generator が使う)
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:screener-runner@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

作成したアドレス
(`screener-runner@<PROJECT_ID>.iam.gserviceaccount.com`)を、
**対象スプレッドシート2つの共有設定に「編集者」として追加する。**
これを忘れると `SpreadsheetNotFound` で失敗する。

> スプレッドシートは既存のものを共有するのが前提。共有せずに実行すると、
> サービスアカウント自身のドライブに同名の**空のスプレッドシートが新規作成され**、
> そちらに書き込まれてしまう(あなたからは見えない)。

### 3-3. Anthropic APIキーを Secret Manager に登録

```bash
printf '%s' 'sk-ant-...' | gcloud secrets create anthropic-api-key --data-file=-

# 更新するとき
printf '%s' 'sk-ant-...' | gcloud secrets versions add anthropic-api-key --data-file=-
```

### 3-4. デプロイ

```bash
cp deploy/config.sh.example deploy/config.sh
# PROJECT_ID と SERVICE_ACCOUNT を自分の値に書き換える

./deploy/deploy.sh      # 3ジョブすべてをビルド&デプロイ(初回は10分程度)
./deploy/schedule.sh    # Cloud Scheduler を設定
```

`config.sh` は `.gitignore` 済み(環境固有の値のため)。

---

## 4. 日常の操作

### 手動で1回だけ動かす

```bash
REGION=asia-northeast1
gcloud run jobs execute minervini-screener --region=$REGION --wait
gcloud run jobs execute momentum-screener  --region=$REGION --wait
gcloud run jobs execute thesis-generator   --region=$REGION --wait
```

### ログを見る

```bash
# 直近の実行ログ
gcloud beta run jobs logs tail minervini-screener --region=$REGION

# 失敗だけ絞り込む
gcloud logging read \
  'resource.type="cloud_run_job" AND resource.labels.job_name="minervini-screener" AND severity>=ERROR' \
  --limit=20 --format='table(timestamp, textPayload)'
```

コンソール: <https://console.cloud.google.com/run/jobs>

### コードを直したあと

```bash
./deploy/deploy.sh minervini   # 1ジョブだけ再デプロイ (minervini / momentum / thesis)
./deploy/deploy.sh             # まとめて再デプロイ
```

### スケジュールを変える

`deploy/config.sh` の `*_SCHEDULE`(UTCのcron)を書き換えて `./deploy/schedule.sh` を再実行。
`schedule.sh` は何度実行しても同じ状態になる。

```bash
gcloud scheduler jobs list --location=$REGION            # 一覧
gcloud scheduler jobs pause  minervini-daily --location=$REGION   # 一時停止
gcloud scheduler jobs resume minervini-daily --location=$REGION   # 再開
gcloud scheduler jobs run    minervini-daily --location=$REGION   # 今すぐ実行
```

**JST → UTC の変換:** JSTから9時間引く。日付をまたぐ場合は曜日指定もずらすこと。
例) JST 平日 08:30 に結果が欲しい → UTC 23:30 の**前日**、つまり `30 23 * * 1-5`。

### ローカルで試す

```bash
gcloud auth application-default login \
  --scopes=https://www.googleapis.com/auth/spreadsheets,https://www.googleapis.com/auth/drive,https://www.googleapis.com/auth/cloud-platform

pip install -r requirements.txt
python -m screeners.minervini_screener      # ルートディレクトリで実行すること
python -m screeners.momentum_screener
ANTHROPIC_API_KEY=sk-ant-... python -m research.generate_thesis
```

---

## 5. 失敗にどう気付くか

### 「実行ステータス」タブ

各スプレッドシートに `実行ステータス` タブが自動で作られ、実行のたびに1行(新しい順)記録される。

| 実行日時(JST) | ジョブ | ステータス | 詳細 |
|---|---|---|---|
| 2026-08-11 08:32:10 JST | minervini-screener | 成功 | 42銘柄 / BREAKOUT:3 ALERT:7 ... |
| 2026-08-10 08:31:55 JST | minervini-screener | 取得失敗 | RuntimeError: Finvizの一次選別が全フィルタ候補で失敗... |

**朝シートを開いたとき、このタブの一番上を見れば前回の実行結果がわかる。**
既存の抽出結果は上書きされないので、失敗しても前日のデータは残る。

### リトライ

Finviz と yfinance はGCPのデータセンターIPからだとレート制限/ブロックに遭うことがある。
そのため取得系の呼び出しは**失敗時に30秒待って1回だけ再試行**する
(`RETRY_COUNT` / `RETRY_WAIT_SEC` 環境変数で調整可能)。

さらに、**「取得できなかった」を「本日は該当銘柄なし」と誤認しない**ようにしてある:

- Finvizの3つのフィルタ候補が全滅した場合 → エラーとして扱う(第3候補はテクニカルのみなので、正常時にゼロ件はあり得ない)
- yfinanceの取得成功率が50%未満 → エラーとして扱い、中途半端なデータで上書きしない

### メール通知(任意)

```bash
./deploy/alert.sh your-address@example.com
```

ログベース指標を作るところまで自動化してある。アラートポリシー自体は
表示されるURLからコンソールで作成する。最初はコンソールの実行履歴を
時々見る運用でも構わない。

---

## 6. 投資テーゼ生成 (thesis-generator)

- 「抽出結果」タブを読み、**上から最大15銘柄**(`THESIS_MAX_TICKERS`)についてテーゼを生成する。
  抽出結果は既に「判定(買い場の近さ)→ 総合スコア降順」で並んでいるので、その順序をそのまま使う
  (ここで独自の優先順位付けはしていない)。
- 各銘柄について、シート上のRS Rating / VCP Grade / VCPスコア / 総合スコア / ファンダ点 /
  EPS成長 / 売上成長 / ROE / 利益率 / 各種乖離率をテキスト化してClaudeに渡す。
  トレンドテンプレートは、抽出結果に載っている時点で8/8通過している。
- 出力は「投資テーゼ」タブに**追記**(過去のテーゼを消さず時系列で残す)。

| 日時 | 銘柄 | 強気シナリオ | 弱気シナリオ | ベースシナリオ | テーゼ | 無効化条件 | 確信度 |
|---|---|---|---|---|---|---|---|

環境変数で調整できる項目:

| 変数 | 既定値 | 意味 |
|---|---|---|
| `THESIS_MAX_TICKERS` | `15` | 1回の実行でAPIを呼ぶ最大銘柄数 |
| `THESIS_MODEL` | `claude-sonnet-5` | 質重視ならOpus系に変更 |
| `THESIS_SPREADSHEET` | `ミネルヴィニ銘柄スクリーナー` | 読み取り元/書き込み先 |
| `THESIS_SHEET` | `投資テーゼ` | 出力タブ名 |

変更するには:

```bash
gcloud run jobs update thesis-generator --region=$REGION \
  --set-env-vars=THESIS_MAX_TICKERS=25
```

---

## 7. よくあるエラー

| 症状 | 原因と対処 |
|---|---|
| `SpreadsheetNotFound` / 空のシートが作られる | サービスアカウントをスプレッドシートに「編集者」で共有していない(3-2参照) |
| `403 Permission denied` (Secret Manager) | 実行用SAに `roles/secretmanager.secretAccessor` が無い(3-2参照) |
| `ANTHROPIC_API_KEY が空です` | `--set-secrets` が未設定。`./deploy/deploy.sh thesis` で再デプロイ |
| `Finvizの一次選別が全フィルタ候補で失敗` | FinvizにブロックされたかFinviz側の仕様変更。時間を空けて再実行し、続くようならリージョン変更を検討 |
| `株価取得の成功が N/M ティッカーのみ` | yfinanceのレート制限。`momentum_screener.py` の `CHUNK_SIZE` を小さくする |
| Cloud Schedulerが `PERMISSION_DENIED` | `./deploy/schedule.sh` を再実行(`roles/run.invoker` を付け直す) |
| ジョブがタイムアウト | `deploy.sh` の `--task-timeout` を伸ばす(現在: minervini 900秒 / 他 1800秒) |

---

## 8. Colab版からの変更点(実行環境まわりのみ)

- `!pip install` / `subprocess.run([... "pip", "install" ...])` を削除 → `requirements.txt` に集約
- `google.colab.auth` を削除 → `google.auth.default(scopes=[...])` に置き換え
- ノートブック的な `if __name__ == "__main__" or True:` を `main()` + エントリポイントに整理
- `/content/alerts_watchlist.txt` → `ALERTS_OUTPUT_PATH`(既定 `/tmp/alerts_watchlist.txt`)。
  コンテナは実行ごとに破棄されるため、書けなくても処理は継続する
- `gspread` 6系に合わせて `ws.update()` をキーワード引数に修正(5系の引数順のままだと動かないため)
- 取得系のリトライ、静かな失敗の検出、「実行ステータス」タブへの記録を追加
- `pandas` を `<3` に固定(Colab版と計算結果を一致させるため)

**スコアリング・フィルタ条件・閾値は変更していない。**
`atr_pct` / `zigzag_atr` / `detect_vcp` / `triage` / `classify_stage` / `weighted_return` /
`_to_pct` / `score_fundamentals` / `merge_fund` / `momentum_score` / `classify` / `transition` /
`rsi_wilder` / `cross_status` / `pct_change_n` は、旧版と同じ入力に対して同じ出力を返すことを確認済み。
