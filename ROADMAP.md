# 全体ロードマップ: SEPA自動化プロジェクト

このファイルは**あなた自身が読むための地図**です。技術的な実装手順は `HANDOFF_PROMPT.md`
（Claude Code向け）にあり、こちらはそれを含めた全体像と進め方を整理したものです。

## ゴール

Colabで毎日手動実行しているスクリーニングを、Google Cloud上で完全自動化する。
その上で、The Claude Portfolio(X: @theaiportfolios)がやっている「AIがリサーチ、
人間がルールと最終判断」というスタイルを、既存のSEPAスクリーニングに接続する。

## The Claude Portfolio方式との対応関係

| The Claude Portfolioの構造 | このプロジェクトでの実装 |
|---|---|
| 人間が事前にリスクルールを固定(ポジション上限・回転率上限など) | あなた自身が決める(下記「残っている宿題」参照。コード化はしない) |
| Claude Codeが週2回、強気/弱気/ベースシナリオ+反証可能なテーゼを作成 | `research/generate_thesis.py` が同じ構造(bull_case/bear_case/base_case/invalidation_condition)をClaude APIで生成し、週2回自動実行 |
| 最終判断と発注は人間 | 変更なし。スプレッドシートを見てあなたが手動発注 |
| 運営者への質疑応答で透明性を担保 | 個人用なので不要。ただし後で見返せるよう、テーゼは全てシートに時系列で残す |

## 進め方: 3ステップ

### ステップ1: 準備(あなたが今やること、15分程度)

`HANDOFF_PROMPT.md`の「前提」セクションに書いた3点:

1. `gcloud auth login` でログイン
2. スクリプト実行用サービスアカウントを作成し、対象スプレッドシート2つに編集者として共有
3. Anthropic APIキーをSecret Managerに登録

これが終わっていないと、Claude Codeが作業を始められません。

### ステップ2: Claude Codeに実行してもらう

`project`フォルダ全体(このROADMAP.md含む)をClaude Codeの作業ディレクトリとして開き、
`HANDOFF_PROMPT.md`の内容をそのまま貼って渡す。Claude Codeが以下を自走で構築する:

- Colab依存コードの除去
- Cloud Run Jobsへのデプロイ(ミネルヴィニ・スクリーナー / モメンタムスクリーナー)
- Cloud Schedulerでの定期実行設定
- generate_thesis.pyの接続(週2回、ショートリストに対してテーゼ生成)

作業中に詰まったログやエラーが出たら、そのままこちらに貼ってもらえれば一緒に見ます。

### ステップ3: 運用開始後の生活

- 平日朝、Colabを開かなくてもスプレッドシートが自動更新されている状態になる
- 火・金曜だけ、「投資テーゼ」タブにClaudeが生成した強気/弱気/ベースシナリオ+テーゼが追加される
- あなたのタスクは「スプレッドシートを見て、判断して、手動発注する」だけに絞られる

## 残っている宿題(コードでは代替できない、あなた自身で決めること)

The Claude Portfolio方式の肝は「ルールを人間が先に固定してからAIに分析させる」ことです。
以下は今回のコードには含めていないので、運用を始める前か、始めながらでも決めてください。
決めたらここに書き足しておくと、後で見返せます。

- [ ] 1銘柄あたりの最大ポジションサイズ(口座の何%まで)
- [ ] 月間の入れ替え上限(何銘柄まで新規/入れ替えしてよいか)
- [ ] `generate_thesis.py`を回す曜日(現在は火・金で仮置き。変更する場合はHANDOFF_PROMPT.md Phase 6の
      Cloud Schedulerスケジュールも合わせて変える)
- [ ] テーゼの「無効化条件」に抵触した銘柄をどう扱うか(自動アラートは今回のスコープ外。
      当面は週次で自分の目でチェックする運用でよい)

## ファイル構成

```
.
  ROADMAP.md               ← このファイル(あなた向け、全体像)
  HANDOFF_PROMPT.md         ← Claude Code向け技術手順書(作業済み)
  README.md                 ← 運用手順書。日々の操作はこちらを見る
  common.py                 ← 認証/リトライ/実行ステータス記録(3ジョブ共通)
  requirements.txt / Procfile
  screeners/
    minervini_screener.py   ← 移行済み(トレンドテンプレート+VCP+ファンダ)
    momentum_screener.py    ← 移行済み(セクター・銘柄別モメンタム)
    requirements.txt
  research/
    generate_thesis.py      ← ショートリスト接続済み(Claude Portfolio方式のテーゼ生成)
    requirements.txt
  deploy/
    config.sh.example       ← コピーして config.sh を作り、PROJECT_ID等を設定
    deploy.sh               ← Cloud Run Jobs へデプロイ
    schedule.sh             ← Cloud Scheduler を設定
    alert.sh                ← 失敗時メール通知(任意)
```

## 現在の状況

ステップ2(Claude Codeによる構築)のうち、**コード側は完了**している。
残っているのは、あなたの環境で実際にデプロイを流すところ:

```bash
cp deploy/config.sh.example deploy/config.sh   # PROJECT_ID / SERVICE_ACCOUNT を記入
./deploy/deploy.sh
./deploy/schedule.sh
gcloud run jobs execute minervini-screener --region=asia-northeast1 --wait
```

詳しい手順とトラブルシューティングは `README.md` を参照。
