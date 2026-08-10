#!/usr/bin/env bash
#
# Cloud Run Jobs へのデプロイ。
#   ./deploy/deploy.sh              … 3ジョブすべてをデプロイ
#   ./deploy/deploy.sh minervini    … 指定したジョブだけデプロイ
#
# Dockerfileは書かない。buildpacks がリポジトリ全体からイメージを作る。
# 3ジョブとも同じソース(=同じイメージ)で、起動コマンドだけが違う。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="${ROOT}/deploy/config.sh"

if [[ ! -f "${CONFIG}" ]]; then
  echo "エラー: ${CONFIG} がありません。" >&2
  echo "  cp deploy/config.sh.example deploy/config.sh" >&2
  echo "を実行して、PROJECT_ID と SERVICE_ACCOUNT を設定してください。" >&2
  exit 1
fi
# shellcheck source=/dev/null
source "${CONFIG}"

: "${PROJECT_ID:?config.sh に PROJECT_ID を設定してください}"
: "${SERVICE_ACCOUNT:?config.sh に SERVICE_ACCOUNT を設定してください}"
REGION="${REGION:-asia-northeast1}"
ANTHROPIC_SECRET_NAME="${ANTHROPIC_SECRET_NAME:-anthropic-api-key}"
THESIS_MAX_TICKERS="${THESIS_MAX_TICKERS:-15}"

TARGET="${1:-all}"

deploy_job() {
  local name="$1" module="$2" timeout="$3" memory="$4"
  shift 4
  echo ""
  echo "=== ${name} をデプロイ中 (python -m ${module}) ==="
  gcloud run jobs deploy "${name}" \
    --project="${PROJECT_ID}" \
    --source="${ROOT}" \
    --region="${REGION}" \
    --service-account="${SERVICE_ACCOUNT}" \
    --task-timeout="${timeout}" \
    --memory="${memory}" \
    --max-retries=1 \
    --command=python \
    --args="-m,${module}" \
    "$@"
}

if [[ "${TARGET}" == "all" || "${TARGET}" == "minervini" ]]; then
  # Anthropic APIキーは不要(最小権限のためシークレットを渡さない)
  deploy_job minervini-screener screeners.minervini_screener 900 1Gi
fi

if [[ "${TARGET}" == "all" || "${TARGET}" == "momentum" ]]; then
  # 約190ティッカー × 3スナップショットで時間がかかるため、タイムアウトを長めに取る
  deploy_job momentum-screener screeners.momentum_screener 1800 2Gi
fi

if [[ "${TARGET}" == "all" || "${TARGET}" == "thesis" ]]; then
  deploy_job thesis-generator research.generate_thesis 1800 1Gi \
    --set-secrets="ANTHROPIC_API_KEY=${ANTHROPIC_SECRET_NAME}:latest" \
    --set-env-vars="THESIS_MAX_TICKERS=${THESIS_MAX_TICKERS}"
fi

echo ""
echo "デプロイ完了。手動実行して動作確認してください:"
echo "  gcloud run jobs execute minervini-screener --region=${REGION} --wait"
echo "  gcloud run jobs execute momentum-screener  --region=${REGION} --wait"
echo "  gcloud run jobs execute thesis-generator   --region=${REGION} --wait"
