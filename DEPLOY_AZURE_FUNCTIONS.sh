#!/usr/bin/env bash
set -euo pipefail

# Usage:
#  ./DEPLOY_AZURE_FUNCTIONS.sh <SUBSCRIPTION_ID> <RG_NAME> <AZ_REGION> <ACR_NAME> <PLAN_NAME> <FUNC_APP_NAME>

SUBSCRIPTION_ID=${1:-}
RG=${2:-}
REGION=${3:-}
ACR=${4:-}
PLAN=${5:-}
APP=${6:-}

if [[ -z "$SUBSCRIPTION_ID" || -z "$RG" || -z "$REGION" || -z "$ACR" || -z "$PLAN" || -z "$APP" ]]; then
  echo "Missing args. Example:" >&2
  echo "  ./DEPLOY_AZURE_FUNCTIONS.sh 00000000-0000-0000-0000-000000000000 my-rg eastus myacrname myplan myfuncapp" >&2
  exit 1
fi

az account set --subscription "$SUBSCRIPTION_ID"
az group create -n "$RG" -l "$REGION"
az acr create -n "$ACR" -g "$RG" --sku Basic || true
az acr login -n "$ACR"

docker build -t "$ACR".azurecr.io/indeed-scraper-func:latest .
docker push "$ACR".azurecr.io/indeed-scraper-func:latest

az functionapp plan create -g "$RG" -n "$PLAN" --location "$REGION" --number-of-workers 1 --sku EP1 --is-linux || true

az functionapp create -g "$RG" -p "$PLAN" -n "$APP" \
  --runtime python --functions-version 4 \
  --deployment-container-image-name "$ACR".azurecr.io/indeed-scraper-func:latest || true

az acr update -n "$ACR" --admin-enabled true
ACR_USER=$(az acr credential show -n "$ACR" --query "username" -o tsv)
ACR_PASS=$(az acr credential show -n "$ACR" --query "passwords[0].value" -o tsv)

az functionapp config container set -g "$RG" -n "$APP" \
  --docker-custom-image-name "$ACR".azurecr.io/indeed-scraper-func:latest \
  --docker-registry-server-url https://"$ACR".azurecr.io \
  --docker-registry-server-user "$ACR_USER" \
  --docker-registry-server-password "$ACR_PASS"

echo "Set required app settings with your proxy and tuning values, e.g.:"
echo "  az functionapp config appsettings set -g $RG -n $APP --settings \"PROXY_URL=http://USER:PASS@HOST:PORT\" \"ACCEPT_LANGUAGE=en-US,en;q=0.9\" \"MIN_DELAY=2.0\" \"BACKOFF_MIN=2.0\" \"BACKOFF_MAX=8.0\""

echo "Get default function key:"
echo "  az functionapp function keys list -g $RG -n $APP --function-name HttpScrape --query default -o tsv"

echo "Invoke: https://$APP.azurewebsites.net/api/jobs?query=python&location=USA&max_results=5&code=<KEY>"
