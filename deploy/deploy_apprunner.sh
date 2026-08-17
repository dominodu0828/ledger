#!/usr/bin/env bash
# Build, push to ECR, and deploy to AWS App Runner.
# Run from the repo root:  bash deploy/deploy_apprunner.sh
set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
REPO="ledger"
SERVICE="ledger"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
REGISTRY="${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com"
IMAGE="${REGISTRY}/${REPO}:latest"

echo "==> account ${ACCOUNT}, region ${REGION}"

aws ecr describe-repositories --repository-names "$REPO" --region "$REGION" >/dev/null 2>&1 \
  || aws ecr create-repository --repository-name "$REPO" --region "$REGION" >/dev/null
echo "==> ECR repo ready"

aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$REGISTRY"

docker build -f deploy/Dockerfile -t "$REPO" .
docker tag "$REPO:latest" "$IMAGE"
docker push "$IMAGE"
echo "==> pushed ${IMAGE}"

# Environment for the running container. Read from the local .env so the
# secrets never enter the image.
set -a; source .env; set +a
ENV_JSON=$(cat <<EOF
{
  "COCKROACH_DSN": "${COCKROACH_DSN}",
  "AWS_REGION": "${REGION}",
  "AWS_ACCESS_KEY_ID": "${AWS_ACCESS_KEY_ID}",
  "AWS_SECRET_ACCESS_KEY": "${AWS_SECRET_ACCESS_KEY}",
  "BEDROCK_EMBED_MODEL": "${BEDROCK_EMBED_MODEL}",
  "BEDROCK_CHAT_MODEL": "${BEDROCK_CHAT_MODEL}",
  "CHAT_BACKEND": "${CHAT_BACKEND}",
  "SCREEN_THRESHOLD": "${SCREEN_THRESHOLD}"
}
EOF
)

ARN="$(aws apprunner list-services --region "$REGION" \
  --query "ServiceSummaryList[?ServiceName=='${SERVICE}'].ServiceArn | [0]" --output text)"

if [ "$ARN" = "None" ] || [ -z "$ARN" ]; then
  echo "==> creating App Runner service"
  aws apprunner create-service --region "$REGION" \
    --service-name "$SERVICE" \
    --source-configuration "{
      \"ImageRepository\": {
        \"ImageIdentifier\": \"${IMAGE}\",
        \"ImageRepositoryType\": \"ECR\",
        \"ImageConfiguration\": {
          \"Port\": \"8080\",
          \"RuntimeEnvironmentVariables\": ${ENV_JSON}
        }
      },
      \"AutoDeploymentsEnabled\": false,
      \"AuthenticationConfiguration\": {
        \"AccessRoleArn\": \"arn:aws:iam::${ACCOUNT}:role/service-role/AppRunnerECRAccessRole\"
      }
    }" \
    --health-check-configuration '{"Protocol":"HTTP","Path":"/healthz","Interval":10,"Timeout":5,"HealthyThreshold":1,"UnhealthyThreshold":5}' \
    --query 'Service.ServiceUrl' --output text
else
  echo "==> updating existing service"
  aws apprunner start-deployment --region "$REGION" --service-arn "$ARN" >/dev/null
  aws apprunner describe-service --region "$REGION" --service-arn "$ARN" \
    --query 'Service.ServiceUrl' --output text
fi

echo
echo "==> demo URL will be https://<the value printed above>"
echo "    (first deployment takes 3-5 minutes to reach RUNNING)"
