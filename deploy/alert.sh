#!/usr/bin/env bash
#
# ジョブ失敗時にメール通知するログベースアラートを作成する(任意)。
#   ./deploy/alert.sh your-address@example.com
#
# Cloud Run Job の実行が失敗したときに出るログを検知して通知する。
# 最初は Cloud Run コンソールの実行履歴を時々見る運用でも構わない。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="${ROOT}/deploy/config.sh"
# shellcheck source=/dev/null
source "${CONFIG}"

EMAIL="${1:-}"
if [[ -z "${EMAIL}" ]]; then
  echo "使い方: ./deploy/alert.sh your-address@example.com" >&2
  exit 1
fi

: "${PROJECT_ID:?config.sh に PROJECT_ID を設定してください}"

echo "=== 通知チャネル(メール)を作成中: ${EMAIL} ==="
CHANNEL_ID="$(gcloud beta monitoring channels create \
  --project="${PROJECT_ID}" \
  --display-name="Screener Alerts" \
  --type=email \
  --channel-labels="email_address=${EMAIL}" \
  --format="value(name)")"
echo "  チャネル: ${CHANNEL_ID}"

echo "=== ログベース指標を作成中 ==="
gcloud logging metrics create screener_job_failures \
  --project="${PROJECT_ID}" \
  --description="Cloud Run Jobs (screeners) の失敗回数" \
  --log-filter='resource.type="cloud_run_job"
severity>=ERROR
resource.labels.job_name=~"minervini-screener|momentum-screener|thesis-generator"' \
  || echo "  (既に存在するためスキップ)"

echo ""
echo "指標を作成しました。アラートポリシーはコンソールから作成してください:"
echo "  https://console.cloud.google.com/monitoring/alerting/policies/create?project=${PROJECT_ID}"
echo "  条件: ログベース指標 logging/user/screener_job_failures が 0 より大きい"
echo "  通知チャネル: ${CHANNEL_ID}"
