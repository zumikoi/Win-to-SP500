#!/usr/bin/env bash
#
# Cloud Scheduler の設定。deploy/deploy.sh の後に1回だけ実行すればよい。
# 何度実行しても同じ状態になる(既存があれば update、無ければ create)。
#
#   ./deploy/schedule.sh
#
# スケジュールを変えたいときは deploy/config.sh の *_SCHEDULE を書き換えて
# もう一度これを実行する。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="${ROOT}/deploy/config.sh"

if [[ ! -f "${CONFIG}" ]]; then
  echo "エラー: ${CONFIG} がありません。deploy/config.sh.example をコピーして作成してください。" >&2
  exit 1
fi
# shellcheck source=/dev/null
source "${CONFIG}"

: "${PROJECT_ID:?config.sh に PROJECT_ID を設定してください}"
REGION="${REGION:-asia-northeast1}"
SCHEDULER_SA="${SCHEDULER_SA:-scheduler-invoker@${PROJECT_ID}.iam.gserviceaccount.com}"
SCHEDULER_SA_ID="${SCHEDULER_SA%%@*}"

MINERVINI_SCHEDULE="${MINERVINI_SCHEDULE:-30 23 * * 1-5}"
MOMENTUM_SCHEDULE="${MOMENTUM_SCHEDULE:-45 23 * * 1-5}"
THESIS_SCHEDULE="${THESIS_SCHEDULE:-0 0 * * 2,5}"

# ---- 1. Scheduler用サービスアカウント(無ければ作成) ----
if ! gcloud iam service-accounts describe "${SCHEDULER_SA}" \
       --project="${PROJECT_ID}" >/dev/null 2>&1; then
  echo "=== Scheduler用サービスアカウントを作成: ${SCHEDULER_SA} ==="
  gcloud iam service-accounts create "${SCHEDULER_SA_ID}" \
    --project="${PROJECT_ID}" \
    --display-name="Cloud Scheduler Job Invoker"
else
  echo "Scheduler用サービスアカウントは作成済み: ${SCHEDULER_SA}"
fi

# ---- 2. 各ジョブに run.invoker を付与 ----
for job in minervini-screener momentum-screener thesis-generator; do
  echo "=== ${job} に roles/run.invoker を付与 ==="
  gcloud run jobs add-iam-policy-binding "${job}" \
    --project="${PROJECT_ID}" \
    --region="${REGION}" \
    --member="serviceAccount:${SCHEDULER_SA}" \
    --role="roles/run.invoker" >/dev/null
done

# ---- 3. スケジュール作成/更新 ----
upsert_schedule() {
  local sched_name="$1" job_name="$2" cron="$3"
  local uri="https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT_ID}/jobs/${job_name}:run"
  local verb="create"

  if gcloud scheduler jobs describe "${sched_name}" \
       --project="${PROJECT_ID}" --location="${REGION}" >/dev/null 2>&1; then
    verb="update"
  fi

  echo "=== スケジュール ${verb}: ${sched_name} ('${cron}' UTC → ${job_name}) ==="
  gcloud scheduler jobs "${verb}" http "${sched_name}" \
    --project="${PROJECT_ID}" \
    --location="${REGION}" \
    --schedule="${cron}" \
    --time-zone="UTC" \
    --uri="${uri}" \
    --http-method=POST \
    --oauth-service-account-email="${SCHEDULER_SA}"
}

upsert_schedule minervini-daily minervini-screener "${MINERVINI_SCHEDULE}"
upsert_schedule momentum-daily  momentum-screener  "${MOMENTUM_SCHEDULE}"
upsert_schedule thesis-biweekly thesis-generator   "${THESIS_SCHEDULE}"

echo ""
echo "設定完了。一覧の確認:"
echo "  gcloud scheduler jobs list --location=${REGION}"
echo "スケジュールを待たずに1回だけ動かす:"
echo "  gcloud scheduler jobs run minervini-daily --location=${REGION}"
