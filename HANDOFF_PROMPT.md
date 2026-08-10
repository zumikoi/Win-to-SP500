# 引き継ぎプロンプト: Colab依存を脱却してGoogle Cloud上で自動化する

このディレクトリ内で、以下のタスクを実行してください。

## 背景

現在、2つのGoogle Colab単一セルスクリプトを毎日手動実行して株式スクリーニングを行っている。

- `minervini_screener.py` : Finviz一次選別 → yfinanceで株価取得 → トレンドテンプレート8条件
  + VCP検出 + ファンダメンタルスコア + 地合い判定 → Googleスプレッドシート「ミネルヴィニ銘柄スクリーナー」
  の「抽出結果」タブへ出力
- `momentum_screener.py` : セクターETF・個別銘柄のモメンタムスコアを現在/1週間前/1ヶ月前の3時点で算出
  → Googleスプレッドシート「モメンタムスクリーナー」の複数タブへ出力
- `research/generate_thesis.py` : Claude APIで個別銘柄の強気/弱気/ベースシナリオと反証可能な投資テーゼを
  生成し、スプレッドシートに追記するモジュール(週1-2回だけ実行する想定)

**課題**: 毎回Colabを開いて実行ボタンを押し、完了(数分)まで画面を閉じられないのが煩わしい。
これをGoogle Cloud上での自動実行に置き換え、完全に人手を介さずスプレッドシートが更新される状態にしたい。

**アーキテクチャ方針**: 実行環境はGitHub Actionsではなく **Cloud Run Jobs + Cloud Scheduler**
(Google Cloudの中だけで完結させる)。理由: サービスアカウントをCloud Run Jobsに直接アタッチできるため、
JSON鍵の発行やWorkload Identity Federationの設定が一切不要になる。GitHubリポジトリ化も必須ではない
(ローカルディレクトリから直接 `gcloud run jobs deploy --source=.` でデプロイできるため)。

**やってはいけないこと**: スコアリングのロジック・フィルタ条件・閾値など、投資判断に関わる計算式は
一切変更しない。今回はあくまで実行環境の移行(Colab → Cloud Run Jobs)のみが目的。

## 前提(ユーザー側で準備済み、または一緒に確認すること)

- Google Cloudプロジェクトが1つある(既存のものでよい)
- 以下のAPIが有効化されている: Cloud Run Admin API, Cloud Scheduler API, Secret Manager API,
  Google Sheets API, Google Drive API, Cloud Build API
  (有効化されていなければ `gcloud services enable run.googleapis.com cloudscheduler.googleapis.com
  secretmanager.googleapis.com sheets.googleapis.com drive.googleapis.com cloudbuild.googleapis.com`)
- スクリプト実行用のサービスアカウントを1つ作成し(鍵は発行しない)、対象スプレッドシート2つの共有設定に
  そのメールアドレスを「編集者」として追加済み
- Anthropic APIキーがSecret Managerに登録されている
  (`gcloud secrets create anthropic-api-key --data-file=-` のような形で、値だけ登録)
- ローカル(またはこの作業環境)で `gcloud auth login` 済み、対象プロジェクトが
  `gcloud config set project <PROJECT_ID>` で設定済み

上記が未実施なら、コード作業に入る前にユーザーと一緒に確認・実施すること。

## タスク

### Phase 1: ディレクトリ構成の整理

```
project/
  screeners/
    minervini_screener.py
    momentum_screener.py
    requirements.txt
  research/
    generate_thesis.py
    requirements.txt
  README.md
```

GitHubへのpushは必須ではない。ローカルのgitでバージョン管理だけしておくのは推奨(ロールバックのため)。

### Phase 2: Colab依存コードの除去

各スクリプトについて:

1. `!pip install ...` や `subprocess.run([sys.executable, "-m", "pip", "install", ...])` を全て削除し、
   使用ライブラリを `requirements.txt` にまとめる(下記参照)。
2. `from google.colab import auth` / `auth.authenticate_user()` / `google.auth.default()` を、
   以下のような形に置き換える:

   ```python
   import google.auth
   import gspread

   creds, _ = google.auth.default(scopes=[
       "https://www.googleapis.com/auth/spreadsheets",
       "https://www.googleapis.com/auth/drive",
   ])
   gc = gspread.authorize(creds)
   ```

   Cloud Run Job実行時は、アタッチされたサービスアカウントの認証情報が自動的に使われるため、
   JSON鍵を明示的に読み込むコードは書かない。ローカルでテストする場合のみ事前に
   `gcloud auth application-default login` を実行しておけば同じコードで動く。
3. `python screeners/minervini_screener.py` のように単体でCLI実行できることを確認する
   (ノートブック特有の記法が残っていないか確認)。
4. print文はそのまま残してよい(Cloud Loggingで確認するため)。

`requirements.txt` の内容案(実際のimportを確認して過不足があれば調整):

```
pandas
numpy
yfinance
finvizfinance
gspread
gspread-formatting
google-auth
anthropic
```

### Phase 3: Cloud Run Jobsへのデプロイ

Dockerfileは手書きしない。buildpacksでソースから直接ビルドする:

```bash
PROJECT_ID="<プロジェクトID>"
REGION="asia-northeast1"
SERVICE_ACCOUNT="<スクリプト実行用サービスアカウントのメールアドレス>"

gcloud run jobs deploy minervini-screener \
  --source=./screeners \
  --region="${REGION}" \
  --service-account="${SERVICE_ACCOUNT}" \
  --set-secrets=ANTHROPIC_API_KEY=anthropic-api-key:latest \
  --task-timeout=900 \
  --memory=1Gi \
  --command=python \
  --args=minervini_screener.py

gcloud run jobs deploy momentum-screener \
  --source=./screeners \
  --region="${REGION}" \
  --service-account="${SERVICE_ACCOUNT}" \
  --task-timeout=900 \
  --memory=1Gi \
  --command=python \
  --args=momentum_screener.py
```

(2つを1ジョブにまとめるか分けるかはClaude Codeの判断でよい。実行時間・依存関係の重複度合いを見て
妥当な方を選ぶこと。`--args`はスクリプトの引数構成に応じて調整する)

### Phase 4: Cloud Schedulerで定期実行

Cloud Scheduler専用のサービスアカウントを作成し、`roles/run.invoker` を付与してから、
HTTPでCloud Run Jobs実行APIを叩くスケジュールを作成する:

```bash
# Scheduler用サービスアカウント作成
gcloud iam service-accounts create scheduler-invoker \
  --display-name="Cloud Scheduler Job Invoker"

gcloud run jobs add-iam-policy-binding minervini-screener \
  --region="${REGION}" \
  --member="serviceAccount:scheduler-invoker@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/run.invoker"

# スケジュール作成(平日、米国市場引け後を想定。UTC 21:30 = JST翌6:30頃)
gcloud scheduler jobs create http minervini-daily \
  --location="${REGION}" \
  --schedule="30 21 * * 1-5" \
  --time-zone="UTC" \
  --uri="https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT_ID}/jobs/minervini-screener:run" \
  --http-method=POST \
  --oauth-service-account-email="scheduler-invoker@${PROJECT_ID}.iam.gserviceaccount.com"
```

momentum-screenerジョブについても同様のスケジュールを作成する(同じscheduler-invokerを使い回してよい)。

### Phase 5: 動作確認とリスク対応

- `gcloud run jobs execute minervini-screener --region=${REGION}` で手動実行し、
  実際にスプレッドシートが更新されることを確認する。
- Finviz(finvizfinance経由のスクレイピング)とyfinanceは、GCPのようなデータセンターIPからの
  アクセスだとレート制限やブロックに遭う可能性がある(GitHub Actionsと同様のリスク)。
  数回実行してみて失敗が頻発するようなら、簡単なリトライ/バックオフ(例: 失敗時に30秒待って
  1回だけ再試行)を追加する。
- 完全に失敗した場合でも、可能な範囲でスプレッドシートに「取得失敗」等のステータスを書き込んでから
  終了するようにし、サイレントに失敗しないようにする。
- 失敗に気付けるよう、Cloud Run Jobsの実行失敗時にCloud Loggingのログベースアラート
  (Cloud Monitoringでのメール通知)を設定することを検討する。最初は手動でCloud Runコンソールの
  実行履歴を時々確認する運用でも構わない。

### Phase 6: generate_thesis.pyの接続

- `research/generate_thesis.py` を、`minervini_screener.py` のショートリスト
  (トレンドテンプレート通過銘柄)と接続する。具体的には `data_summary_fn` の中身を、
  `minervini_screener.py` の判定結果(RS Rating, VCP Grade, Trend Template通過数など)を
  文字列化する処理に置き換える。
- 別のCloud Run Jobとしてデプロイし、週2回程度(曜日はユーザーに確認、なければ火・金を仮で設定)の
  Cloud Schedulerスケジュールを別途作成する。
- 出力先は既存のスプレッドシートに新しいタブ「投資テーゼ」を追加する形にする。

### Phase 7: README作成

セットアップ手順(サービスアカウントの権限、Secret Managerへの登録、Cloud Schedulerのスケジュール
変更方法、手動実行コマンド)を `README.md` にまとめる。将来ユーザー自身がメンテナンスできるように
するため。

## 完了条件

- 各Cloud Run JobがCloud Schedulerのスケジュール通りに自動実行され、ユーザーが何も操作せずに
  スプレッドシートが更新される状態になっていること
- `gcloud run jobs execute` での手動実行で、既存のColab版と同じ計算結果が出ることを一度は確認すること
- JSON鍵ファイルがどこにも生成・保存されていないこと(サービスアカウントはCloud Run Jobsに
  アタッチする形のみで使用する)
