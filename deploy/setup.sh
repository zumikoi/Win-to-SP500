#!/usr/bin/env bash
#
# これ1本で初回セットアップを最後まで終わらせるスクリプト。
#
#   ./deploy/setup.sh
#
# あなたが手を動かすのは3か所だけ:
#   (1) Anthropic APIキーの貼り付け
#   (2) スプレッドシート2つの共有(ブラウザ作業。画面の指示で一時停止します)
#   (3) 最後の確認
#
# 何度実行しても壊れません(作成済みのものはスキップされます)。
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REGION_DEFAULT="asia-northeast1"
SA_ID="screener-runner"
SECRET_NAME="anthropic-api-key"
SHEET_MINERVINI="ミネルヴィニ銘柄スクリーナー"
SHEET_MOMENTUM="モメンタムスクリーナー"

BOLD=$'\033[1m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RED=$'\033[31m'; CYAN=$'\033[36m'; OFF=$'\033[0m'

step()  { echo ""; echo "${BOLD}${CYAN}━━━ $* ━━━${OFF}"; }
ok()    { echo "  ${GREEN}✓${OFF} $*"; }
warn()  { echo "  ${YELLOW}!${OFF} $*"; }
fail()  { echo ""; echo "${RED}${BOLD}✗ $*${OFF}"; echo ""; exit 1; }

trap 'fail "途中で失敗しました。上のエラーをそのままClaudeに貼り付けてください。"' ERR
set -E

echo ""
echo "${BOLD}=====================================================${OFF}"
echo "${BOLD}  スクリーナー自動化 セットアップ${OFF}"
echo "${BOLD}=====================================================${OFF}"
echo "  所要時間: 15分ほど(うち10分はビルド待ちで放置できます)"

# ---------------------------------------------------------------
step "1/8  Googleアカウントとプロジェクトの確認"
# ---------------------------------------------------------------
command -v gcloud >/dev/null 2>&1 || fail "gcloud が見つかりません。Google Cloud コンソールの「Cloud Shell」から実行してください。"

ACCOUNT="$(gcloud config get-value account 2>/dev/null)"
if [[ -z "${ACCOUNT}" || "${ACCOUNT}" == "(unset)" ]]; then
  warn "ログインしていません。ブラウザが開くので、いつものGoogleアカウントでログインしてください。"
  gcloud auth login
  ACCOUNT="$(gcloud config get-value account 2>/dev/null)"
fi
ok "ログイン中のアカウント: ${ACCOUNT}"

PROJECT_ID="$(gcloud config get-value project 2>/dev/null)"
if [[ -z "${PROJECT_ID}" || "${PROJECT_ID}" == "(unset)" ]]; then
  echo ""
  echo "  使えるプロジェクトの一覧:"
  gcloud projects list --format="table(projectId, name)" 2>/dev/null | sed 's/^/    /'
  echo ""
  read -r -p "  使うプロジェクトIDを入力してEnter: " PROJECT_ID
  [[ -n "${PROJECT_ID}" ]] || fail "プロジェクトIDが空です。"
  gcloud config set project "${PROJECT_ID}" >/dev/null
fi
ok "プロジェクト: ${PROJECT_ID}"

read -r -p "  リージョン [${REGION_DEFAULT}] (そのままでよければEnter): " REGION
REGION="${REGION:-${REGION_DEFAULT}}"
ok "リージョン: ${REGION}"

SERVICE_ACCOUNT="${SA_ID}@${PROJECT_ID}.iam.gserviceaccount.com"

# ---------------------------------------------------------------
step "2/8  必要なAPIを有効化(2〜3分かかります)"
# ---------------------------------------------------------------
echo "  Cloud Run / Scheduler / Secret Manager / Sheets / Drive / Cloud Build"
if ! gcloud services enable \
      run.googleapis.com cloudscheduler.googleapis.com \
      secretmanager.googleapis.com sheets.googleapis.com \
      drive.googleapis.com cloudbuild.googleapis.com \
      --project="${PROJECT_ID}" 2>&1 | sed 's/^/    /'; then
  fail "APIの有効化に失敗しました。プロジェクトで「お支払い(Billing)」が有効になっているか確認してください。
     確認先: https://console.cloud.google.com/billing/linkedaccount?project=${PROJECT_ID}"
fi
ok "APIを有効化しました"

# ---------------------------------------------------------------
step "3/8  実行用サービスアカウントの作成"
# ---------------------------------------------------------------
if gcloud iam service-accounts describe "${SERVICE_ACCOUNT}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
  ok "作成済みです: ${SERVICE_ACCOUNT}"
else
  gcloud iam service-accounts create "${SA_ID}" \
    --project="${PROJECT_ID}" \
    --display-name="Screener Job Runner" >/dev/null
  ok "作成しました: ${SERVICE_ACCOUNT}"
fi

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${SERVICE_ACCOUNT}" \
  --role="roles/secretmanager.secretAccessor" \
  --condition=None >/dev/null 2>&1
ok "Secret Manager の読み取り権限を付与しました"
echo "  ${YELLOW}※ このサービスアカウントに鍵ファイル(JSON)は作りません。${OFF}"

# ---------------------------------------------------------------
step "4/8  Anthropic APIキーの登録"
# ---------------------------------------------------------------
if gcloud secrets describe "${SECRET_NAME}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
  ok "登録済みです(secret名: ${SECRET_NAME})"
  read -r -p "  新しいキーに入れ替えますか? [y/N]: " REPLACE_KEY
  if [[ "${REPLACE_KEY}" =~ ^[Yy]$ ]]; then
    read -r -s -p "  APIキーを貼り付けてEnter (画面には表示されません): " API_KEY; echo ""
    printf '%s' "${API_KEY}" | gcloud secrets versions add "${SECRET_NAME}" \
      --project="${PROJECT_ID}" --data-file=- >/dev/null
    ok "更新しました"
  fi
else
  echo "  Anthropic のコンソール( https://console.anthropic.com/settings/keys )で"
  echo "  発行したキー( sk-ant- で始まる文字列 )を貼り付けてください。"
  echo ""
  read -r -s -p "  APIキーを貼り付けてEnter (画面には表示されません): " API_KEY; echo ""
  [[ -n "${API_KEY}" ]] || fail "APIキーが空です。"
  printf '%s' "${API_KEY}" | gcloud secrets create "${SECRET_NAME}" \
    --project="${PROJECT_ID}" --data-file=- >/dev/null
  ok "登録しました(secret名: ${SECRET_NAME})"
fi

# ---------------------------------------------------------------
step "5/8  【あなたの作業】スプレッドシート2つの共有"
# ---------------------------------------------------------------
cat <<EOS

  下のメールアドレスを、スプレッドシート2つの共有相手に追加してください。

      ${BOLD}${GREEN}${SERVICE_ACCOUNT}${OFF}

  対象のスプレッドシート:
      ・${SHEET_MINERVINI}
      ・${SHEET_MOMENTUM}

  手順(2つとも同じ):
      1. スプレッドシートを開く
      2. 右上の「共有」ボタンをクリック
      3. 上の入力欄に、上記のメールアドレスを貼り付ける
      4. 右側の権限を「${BOLD}編集者${OFF}」に変更する  ← 閲覧者のままだと動きません
      5. 「通知を送信」のチェックを${BOLD}外す${OFF}      ← 外さないとエラーになることがあります
      6. 「送信」(または「共有」)をクリック

  ${YELLOW}※ これを忘れると、エラーにならずに「空の別シート」が作られ、
     あなたのシートは永久に更新されません。一番大事な手順です。${OFF}

EOS
read -r -p "  2つとも共有できたらEnterを押してください: " _

# ---------------------------------------------------------------
step "6/8  設定ファイルの作成"
# ---------------------------------------------------------------
CONFIG="${ROOT}/deploy/config.sh"
cat > "${CONFIG}" <<EOS
# deploy/setup.sh が自動生成しました($(date '+%Y-%m-%d %H:%M'))
PROJECT_ID="${PROJECT_ID}"
SERVICE_ACCOUNT="${SERVICE_ACCOUNT}"
REGION="${REGION}"
SCHEDULER_SA="scheduler-invoker@${PROJECT_ID}.iam.gserviceaccount.com"
ANTHROPIC_SECRET_NAME="${SECRET_NAME}"

# スケジュール(UTC)。JSTに直すには9時間足す。
MINERVINI_SCHEDULE="30 23 * * 1-5"   # JST 平日の翌朝 08:30
MOMENTUM_SCHEDULE="45 23 * * 1-5"    # JST 平日の翌朝 08:45
THESIS_SCHEDULE="0 0 * * 2,5"        # JST 火・金の 09:00

THESIS_MAX_TICKERS="15"
EOS
ok "deploy/config.sh を作成しました"

# ---------------------------------------------------------------
step "7/8  デプロイ(10分ほどかかります。放置してOK)"
# ---------------------------------------------------------------
bash "${ROOT}/deploy/deploy.sh" || fail "デプロイに失敗しました。"
ok "3つのジョブをデプロイしました"

bash "${ROOT}/deploy/schedule.sh" >/dev/null || fail "スケジュール設定に失敗しました。"
ok "自動実行のスケジュールを設定しました"

# ---------------------------------------------------------------
step "8/8  テスト実行(5分ほど)"
# ---------------------------------------------------------------
echo "  ミネルヴィニ・スクリーナーを1回だけ動かして、シートが更新されるか確かめます。"
if gcloud run jobs execute minervini-screener \
     --project="${PROJECT_ID}" --region="${REGION}" --wait 2>&1 | sed 's/^/    /'; then
  TEST_RESULT="成功"
else
  TEST_RESULT="失敗"
fi

echo ""
echo "${BOLD}=====================================================${OFF}"
if [[ "${TEST_RESULT}" == "成功" ]]; then
  echo "${GREEN}${BOLD}  セットアップ完了${OFF}"
  echo "${BOLD}=====================================================${OFF}"
  cat <<EOS

  スプレッドシート「${SHEET_MINERVINI}」を開いて、次の2つを確認してください。

    1. ${BOLD}「抽出結果」タブ${OFF}      … 今日の日付で銘柄が並んでいるか
    2. ${BOLD}「実行ステータス」タブ${OFF} … 一番上の行が「成功」になっているか

  ${YELLOW}もし「抽出結果」タブが更新されていない場合${OFF}
  → 手順5の共有ができていません。共有をやり直して、もう一度これを実行:
       gcloud run jobs execute minervini-screener --region=${REGION} --wait

  ${BOLD}明日以降は何もしなくて構いません。${OFF}
    ・平日 朝08:30  ミネルヴィニ・スクリーナー が自動更新
    ・平日 朝08:45  モメンタム・スクリーナー が自動更新
    ・火・金 朝09:00 投資テーゼ が追記

  残り2つのジョブも今すぐ試すなら:
    gcloud run jobs execute momentum-screener --region=${REGION} --wait
    gcloud run jobs execute thesis-generator  --region=${REGION} --wait

EOS
else
  echo "${RED}${BOLD}  テスト実行が失敗しました${OFF}"
  echo "${BOLD}=====================================================${OFF}"
  cat <<EOS

  デプロイ自体は終わっています。失敗の原因を見るには:

    gcloud run jobs executions list --job=minervini-screener --region=${REGION} --limit=1

  上の出力をそのままClaudeに貼り付けてください。

EOS
fi
