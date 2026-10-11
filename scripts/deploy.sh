#!/usr/bin/env bash
# One-shot deploy: backend stack, data warm-up, and the web app on Amplify Hosting.
# Needs: AWS CLI v2 (configured), AWS SAM CLI, Python 3, curl. Works in Git Bash on Windows.
# Usage:  REPLAY_DATE=2024-05-03 ./scripts/deploy.sh
set -euo pipefail
cd "$(dirname "$0")/.."

STACK=${STACK:-heatshield}
REGION=${AWS_REGION:-ap-south-1}
REPLAY_DATE=${REPLAY_DATE:-2024-05-03}
BBOX=${BBOX:-17.30,78.30,17.56,78.60}
CITY=${CITY:-hyderabad}
APP_NAME=${APP_NAME:-heatshield}
PY=$(command -v python3 || command -v python)
SAM=$(command -v sam || command -v sam.cmd)
TMP=$(mktemp -d)

# aws.exe on Windows ends lines with \r; strip it from everything we capture
aws_() { aws --region "$REGION" "$@" | tr -d '\r'; }

echo "== 1/6 sam deploy ($STACK in $REGION)"
# No third-party dependencies (boto3 ships with Lambda), so no sam build step is needed.
"$SAM" deploy --template-file template.yaml --stack-name "$STACK" --region "$REGION" --resolve-s3 \
  --capabilities CAPABILITY_IAM --no-confirm-changeset --no-fail-on-empty-changeset \
  --parameter-overrides "CityName=$CITY" "BBox=$BBOX" "ReplayDate=$REPLAY_DATE"

out() { aws_ cloudformation describe-stacks --stack-name "$STACK" \
  --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text; }
API_URL=$(out ApiUrl); GRID_FN=$(out GridFunctionName); SEED_FN=$(out SeedFunctionName)
echo "API: $API_URL"

invoke() {
  aws_ lambda invoke --function-name "$1" --cli-binary-format raw-in-base64-out \
    --cli-read-timeout 310 --payload "$2" "$TMP/out.json" >/dev/null
  head -c 600 "$TMP/out.json"; echo
}

echo "== 2/6 forecast grid"
invoke "$GRID_FN" '{"mode":"forecast"}'
echo "== 3/6 replay grid for $REPLAY_DATE"
invoke "$GRID_FN" "{\"mode\":\"replay\",\"date\":\"$REPLAY_DATE\"}"
echo "== 4/6 seed water/shade/fuel points from OpenStreetMap"
invoke "$SEED_FN" '{}'

echo "== 5/6 web bundle"
LOC_KEY=${LOCATION_API_KEY:-}
cat > web/config.js <<EOF
window.HEAT_CONFIG = {
  API_URL: "$API_URL",
  LOCATION_API_KEY: "$LOC_KEY",
  LOCATION_REGION: "$REGION",
  CENTER: [78.4867, 17.3850]
};
EOF
rm -f web.zip
"$PY" -c "import shutil; shutil.make_archive('web', 'zip', 'web')"

echo "== 6/6 Amplify Hosting"
APP_ID=$(aws_ amplify list-apps --query "apps[?name=='$APP_NAME'].appId | [0]" --output text)
if [ -z "$APP_ID" ] || [ "$APP_ID" = "None" ]; then
  APP_ID=$(aws_ amplify create-app --name "$APP_NAME" --query app.appId --output text)
  aws_ amplify create-branch --app-id "$APP_ID" --branch-name main >/dev/null
fi
read -r JOB_ID UPLOAD_URL < <(aws_ amplify create-deployment --app-id "$APP_ID" --branch-name main \
  --query "[jobId, zipUploadUrl]" --output text)
curl -sS --fail -T web.zip -H "Content-Type: application/zip" "$UPLOAD_URL"
aws_ amplify start-deployment --app-id "$APP_ID" --branch-name main --job-id "$JOB_ID" >/dev/null
for _ in $(seq 1 30); do
  STATUS=$(aws_ amplify get-job --app-id "$APP_ID" --branch-name main --job-id "$JOB_ID" --query job.summary.status --output text)
  [ "$STATUS" = "SUCCEED" ] || [ "$STATUS" = "FAILED" ] && break
  sleep 4
done
WEB_URL="https://main.$APP_ID.amplifyapp.com"

echo
echo "Amplify deployment: $STATUS"
echo "App:   $WEB_URL"
echo "Demo:  $WEB_URL/?demo=1&replay=1"
echo "Smoke: curl $API_URL/config"
