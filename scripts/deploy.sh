#!/usr/bin/env bash
# One-shot deploy: backend stack, data warm-up, and a web bundle for Amplify Hosting.
# Needs: AWS CLI v2 (configured), AWS SAM CLI, Python 3.12+, zip.
# Usage:  REPLAY_DATE=2024-05-03 ./scripts/deploy.sh
set -euo pipefail
cd "$(dirname "$0")/.."

STACK=${STACK:-heatshield}
REGION=${AWS_REGION:-ap-south-1}
REPLAY_DATE=${REPLAY_DATE:-2024-05-03}
BBOX=${BBOX:-17.30,78.30,17.56,78.60}
CITY=${CITY:-hyderabad}

echo "== 1/5 sam build + deploy ($STACK in $REGION)"
sam build
sam deploy --stack-name "$STACK" --region "$REGION" --resolve-s3 --capabilities CAPABILITY_IAM \
  --no-confirm-changeset --no-fail-on-empty-changeset \
  --parameter-overrides "CityName=$CITY" "BBox=$BBOX" "ReplayDate=$REPLAY_DATE"

out() { aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text; }
API_URL=$(out ApiUrl); GRID_FN=$(out GridFunctionName); SEED_FN=$(out SeedFunctionName)
echo "API: $API_URL"

invoke() { aws lambda invoke --region "$REGION" --function-name "$1" --cli-binary-format raw-in-base64-out \
  --cli-read-timeout 310 --payload "$2" /dev/stdout | head -c 600; echo; }

echo "== 2/5 forecast grid"
invoke "$GRID_FN" '{"mode":"forecast"}'
echo "== 3/5 replay grid for $REPLAY_DATE"
invoke "$GRID_FN" "{\"mode\":\"replay\",\"date\":\"$REPLAY_DATE\"}"
echo "== 4/5 seed water/shade/fuel points from OpenStreetMap"
invoke "$SEED_FN" '{}'

echo "== 5/5 web bundle"
LOC_KEY=${LOCATION_API_KEY:-}
cat > web/config.js <<EOF
window.HEAT_CONFIG = {
  API_URL: "$API_URL",
  LOCATION_API_KEY: "$LOC_KEY",
  LOCATION_REGION: "$REGION",
  CENTER: [78.4867, 17.3850]
};
EOF
rm -f web.zip && (cd web && zip -qr ../web.zip .)
echo
echo "Done. Upload web.zip in the Amplify console: Create new app -> Deploy without Git -> drag web.zip."
echo "Then open  <amplify-url>/?demo=1&replay=1  for the demo ride."
echo "Smoke test: curl $API_URL/config"
