#!/usr/bin/env bash
set -euo pipefail

RESOURCE_GROUP="${RESOURCE_GROUP:-rg-travel-agent-hosted-demo}"
LOCATION="${LOCATION:-japaneast}"
AZURE_ENV_NAME="${AZURE_ENV_NAME:-travel-agent-local}"
HOSTED_AGENT_NAME="${HOSTED_AGENT_NAME:-travel-request-agent}"
MODEL_DEPLOYMENT_NAME="${MODEL_DEPLOYMENT_NAME:-gpt-5.4}"
MODEL_CAPACITY="${MODEL_CAPACITY:-20}"
MCP_APP_DISPLAY_NAME="${MCP_APP_DISPLAY_NAME:-travel-mcp-functions}"
WEB_APP_DISPLAY_NAME="${WEB_APP_DISPLAY_NAME:-travel-agent-web}"
IMAGE_NAME="${IMAGE_NAME:-travel-agent-app}"

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Required command not found: $1" >&2
    exit 1
  fi
}

ensure_app_registration() {
  local display_name="$1"
  local client_id
  client_id=$(az ad app list \
    --display-name "$display_name" \
    --query '[0].appId' \
    --output tsv)
  if [[ -z "$client_id" ]]; then
    client_id=$(az ad app create \
      --display-name "$display_name" \
      --sign-in-audience AzureADMyOrg \
      --query appId \
      --output tsv)
  fi
  printf '%s' "$client_id"
}

ensure_service_principal() {
  local client_id="$1"
  local principal_id
  for attempt in {1..12}; do
    principal_id=$(az ad sp list \
      --filter "appId eq '${client_id}'" \
      --query '[0].id' \
      --output tsv)
    if [[ -n "$principal_id" ]]; then
      return
    fi
    if az ad sp create --id "$client_id" --output none; then
      return
    fi
    if [[ "$attempt" -eq 12 ]]; then
      echo "Could not create a service principal for ${client_id}." >&2
      exit 1
    fi
    sleep 5
  done
}

ensure_role_assignment() {
  local principal_id="$1"
  local principal_type="$2"
  local role="$3"
  local scope="$4"
  local assignment_id
  assignment_id=$(az role assignment list \
    --assignee-object-id "$principal_id" \
    --scope "$scope" \
    --role "$role" \
    --query '[0].id' \
    --output tsv)
  if [[ -z "$assignment_id" ]]; then
    az role assignment create \
      --assignee-object-id "$principal_id" \
      --assignee-principal-type "$principal_type" \
      --role "$role" \
      --scope "$scope" \
      --output none
  fi
}

require_command az
require_command azd
require_command jq
require_command python
require_command zip

SUBSCRIPTION_ID="${AZURE_SUBSCRIPTION_ID:-$(az account show --query id --output tsv)}"
az account set --subscription "$SUBSCRIPTION_ID"

echo "Creating or reusing Entra app registrations..."
MCP_ENTRA_CLIENT_ID="${MCP_ENTRA_CLIENT_ID:-$(ensure_app_registration "$MCP_APP_DISPLAY_NAME")}"
WEB_ENTRA_CLIENT_ID="${WEB_ENTRA_CLIENT_ID:-$(ensure_app_registration "$WEB_APP_DISPLAY_NAME")}"
ensure_service_principal "$MCP_ENTRA_CLIENT_ID"
ensure_service_principal "$WEB_ENTRA_CLIENT_ID"
az ad app update \
  --id "$MCP_ENTRA_CLIENT_ID" \
  --identifier-uris "api://${MCP_ENTRA_CLIENT_ID}" \
  --output none

echo "Deploying Azure infrastructure to ${LOCATION}..."
az group create \
  --name "$RESOURCE_GROUP" \
  --location "$LOCATION" \
  --output none
DEPLOY_OUTPUT=$(az deployment group create \
  --name "travel-agent-$(date +%Y%m%d%H%M%S)" \
  --resource-group "$RESOURCE_GROUP" \
  --template-file infra/main.bicep \
  --parameters \
    location="$LOCATION" \
    modelName="$MODEL_DEPLOYMENT_NAME" \
    modelCapacity="$MODEL_CAPACITY" \
    mcpEntraClientId="$MCP_ENTRA_CLIENT_ID" \
    webEntraClientId="$WEB_ENTRA_CLIENT_ID" \
  --query properties.outputs \
  --output json)

ACCOUNT_NAME=$(jq -r '.accountName.value' <<<"$DEPLOY_OUTPUT")
PROJECT_NAME=$(jq -r '.projectName.value' <<<"$DEPLOY_OUTPUT")
PROJECT_ENDPOINT=$(jq -r '.projectEndpoint.value' <<<"$DEPLOY_OUTPUT")
APPINSIGHTS_CONNECTION_STRING=$(jq -r '.appInsightsConnectionString.value' <<<"$DEPLOY_OUTPUT")
APP_INSIGHTS_RESOURCE_ID=$(jq -r '.appInsightsResourceId.value' <<<"$DEPLOY_OUTPUT")
ACR_NAME=$(jq -r '.acrName.value' <<<"$DEPLOY_OUTPUT")
ACR_LOGIN_SERVER=$(jq -r '.acrLoginServer.value' <<<"$DEPLOY_OUTPUT")
COSMOS_ENDPOINT=$(jq -r '.cosmosEndpoint.value' <<<"$DEPLOY_OUTPUT")
COSMOS_ACCOUNT_NAME=$(jq -r '.cosmosAccountName.value' <<<"$DEPLOY_OUTPUT")
COSMOS_DATABASE=$(jq -r '.cosmosDatabaseName.value' <<<"$DEPLOY_OUTPUT")
APP_NAME=$(jq -r '.appName.value' <<<"$DEPLOY_OUTPUT")
APP_URL=$(jq -r '.appUrl.value' <<<"$DEPLOY_OUTPUT")
MCP_TOOL_ENDPOINT=$(jq -r '.mcpEndpoint.value' <<<"$DEPLOY_OUTPUT")
FUNCTION_APP_NAME=$(jq -r '.functionAppName.value' <<<"$DEPLOY_OUTPUT")
FUNCTION_STORAGE_ACCOUNT=$(jq -r '.funcStorageAccountName.value' <<<"$DEPLOY_OUTPUT")

echo "Registering the Container Apps Easy Auth callback..."
az ad app update \
  --id "$WEB_ENTRA_CLIENT_ID" \
  --web-redirect-uris "${APP_URL}/.auth/login/aad/callback" \
  --enable-id-token-issuance true \
  --output none

account_user_type=$(az account show --query user.type --output tsv)
account_user_name=$(az account show --query user.name --output tsv)
if [[ "$account_user_type" == "user" ]]; then
  deploy_principal_id=$(az ad signed-in-user show --query id --output tsv)
  deploy_principal_type="User"
else
  deploy_principal_id=$(az ad sp show --id "$account_user_name" --query id --output tsv)
  deploy_principal_type="ServicePrincipal"
fi

ai_account_id=$(az cognitiveservices account show \
  --name "$ACCOUNT_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --query id \
  --output tsv)
ensure_role_assignment \
  "$deploy_principal_id" \
  "$deploy_principal_type" \
  "Foundry Project Manager" \
  "$(jq -r '.projectResourceId.value' <<<"$DEPLOY_OUTPUT")"

echo "Packaging and deploying MCP Functions..."
function_storage_id=$(az storage account show \
  --name "$FUNCTION_STORAGE_ACCOUNT" \
  --resource-group "$RESOURCE_GROUP" \
  --query id \
  --output tsv)
ensure_role_assignment \
  "$deploy_principal_id" \
  "$deploy_principal_type" \
  "Storage Blob Data Contributor" \
  "$function_storage_id"
acr_id=$(az acr show \
  --name "$ACR_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --query id \
  --output tsv)
ensure_role_assignment \
  "$deploy_principal_id" \
  "$deploy_principal_type" \
  "AcrPush" \
  "$acr_id"

DEPLOY_DIR=$(mktemp -d "${TMPDIR:-/tmp}/mcp-deploy.XXXXXX")
DEPLOY_ZIP="${TMPDIR:-/tmp}/mcp-deploy.zip"
cleanup() {
  rm -rf "$DEPLOY_DIR"
  rm -f "$DEPLOY_ZIP"
}
trap cleanup EXIT

cp mcp-tools/function_app.py mcp-tools/host.json "$DEPLOY_DIR/"
cp -R mcp-tools/tools "$DEPLOY_DIR/"
python -m pip install \
  --quiet \
  --requirement mcp-tools/requirements.txt \
  --target "$DEPLOY_DIR/.python_packages/lib/site-packages" \
  --platform manylinux2014_x86_64 \
  --python-version 3.11 \
  --implementation cp \
  --abi cp311 \
  --only-binary=:all:
(cd "$DEPLOY_DIR" && zip -qr "$DEPLOY_ZIP" .)

for attempt in {1..12}; do
  if az storage blob upload \
    --account-name "$FUNCTION_STORAGE_ACCOUNT" \
    --container-name function-releases \
    --file "$DEPLOY_ZIP" \
    --name mcp-deploy.zip \
    --overwrite \
    --auth-mode login \
    --output none; then
    break
  fi
  if [[ "$attempt" -eq 12 ]]; then
    echo "Storage RBAC did not become effective in time." >&2
    exit 1
  fi
  sleep 10
done
az functionapp restart \
  --name "$FUNCTION_APP_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --output none

echo "Building and deploying the authenticated BFF..."
GIT_REVISION="$(git rev-parse --short HEAD 2>/dev/null || true)"
if [[ -z "$GIT_REVISION" ]]; then
  GIT_REVISION="$(date -u +%Y%m%d%H%M%S)"
fi
IMAGE_TAG="${IMAGE_NAME}:${GIT_REVISION}"
az acr build \
  --registry "$ACR_NAME" \
  --image "$IMAGE_TAG" \
  --file app/Dockerfile \
  app \
  --output none
az containerapp registry set \
  --name "$APP_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --server "$ACR_LOGIN_SERVER" \
  --identity system \
  --output none
az containerapp ingress update \
  --name "$APP_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --target-port 8000 \
  --output none
az containerapp update \
  --name "$APP_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --image "${ACR_LOGIN_SERVER}/${IMAGE_TAG}" \
  --output none

echo "Deploying the Foundry Hosted Agent..."
azd extension install azure.ai.projects
azd extension install azure.ai.agents
if ! azd auth login --check-status >/dev/null 2>&1; then
  azd auth login
fi
if ! azd env select "$AZURE_ENV_NAME" --no-prompt >/dev/null 2>&1; then
  azd env new "$AZURE_ENV_NAME" \
    --subscription "$SUBSCRIPTION_ID" \
    --location "$LOCATION" \
    --no-prompt
fi
azd env set AZURE_SUBSCRIPTION_ID "$SUBSCRIPTION_ID"
azd env set AZURE_RESOURCE_GROUP "$RESOURCE_GROUP"
azd env set AZURE_LOCATION "$LOCATION"
azd env set AZURE_AI_PROJECT_NAME "$PROJECT_NAME"
azd env set AZURE_AI_PROJECT_ID "$(jq -r '.projectResourceId.value' <<<"$DEPLOY_OUTPUT")"
azd env set AZURE_AI_PROJECT_ENDPOINT "$PROJECT_ENDPOINT"
azd env set AZURE_CONTAINER_REGISTRY_ENDPOINT "$ACR_LOGIN_SERVER"
azd env set AZURE_CONTAINER_REGISTRY_RESOURCE_ID "$acr_id"
azd env set FOUNDRY_PROJECT_ENDPOINT "$PROJECT_ENDPOINT"
azd env set AZURE_AI_MODEL_DEPLOYMENT_NAME "$MODEL_DEPLOYMENT_NAME"
azd env set MCP_TOOL_ENDPOINT "$MCP_TOOL_ENDPOINT"
azd env set MCP_FUNCTION_APP_CLIENT_ID "$MCP_ENTRA_CLIENT_ID"
azd env set COSMOS_ENDPOINT "$COSMOS_ENDPOINT"
azd env set COSMOS_DATABASE_NAME "$COSMOS_DATABASE"
azd env set COSMOS_CHECKPOINT_CONTAINER "workflow-checkpoints"
azd deploy "$HOSTED_AGENT_NAME" --no-prompt

AZURE_SUBSCRIPTION_ID="$SUBSCRIPTION_ID" \
AZURE_AI_PROJECT_ENDPOINT="$PROJECT_ENDPOINT" \
FOUNDRY_ACCOUNT_RESOURCE_ID="$ai_account_id" \
FOUNDRY_PROJECT_RESOURCE_ID="$(jq -r '.projectResourceId.value' <<<"$DEPLOY_OUTPUT")" \
APP_INSIGHTS_RESOURCE_ID="$APP_INSIGHTS_RESOURCE_ID" \
RESOURCE_GROUP="$RESOURCE_GROUP" \
FOUNDRY_ACCOUNT_NAME="$ACCOUNT_NAME" \
FOUNDRY_PROJECT_NAME="$PROJECT_NAME" \
COSMOS_ACCOUNT_NAME="$COSMOS_ACCOUNT_NAME" \
CONTAINER_APP_NAME="$APP_NAME" \
FUNCTION_APP_NAME="$FUNCTION_APP_NAME" \
HOSTED_AGENT_NAME="$HOSTED_AGENT_NAME" \
  bash scripts/configure-hosted-agent.sh

printf '%s\n' \
  "AZURE_AI_PROJECT_ENDPOINT=${PROJECT_ENDPOINT}" \
  "AZURE_AI_MODEL_DEPLOYMENT_NAME=${MODEL_DEPLOYMENT_NAME}" \
  "HOSTED_AGENT_NAME=${HOSTED_AGENT_NAME}" \
  "COSMOS_ENDPOINT=${COSMOS_ENDPOINT}" \
  "COSMOS_DATABASE=${COSMOS_DATABASE}" \
  "APPLICATIONINSIGHTS_CONNECTION_STRING=${APPINSIGHTS_CONNECTION_STRING}" \
  "MCP_TOOL_ENDPOINT=${MCP_TOOL_ENDPOINT}" \
  "MCP_FUNCTION_APP_CLIENT_ID=${MCP_ENTRA_CLIENT_ID}" \
  "AZURE_TENANT_ID=$(az account show --query tenantId --output tsv)" \
  "ENTRA_CLIENT_ID=${WEB_ENTRA_CLIENT_ID}" \
  > .env
cp .env app/backend/.env
printf '%s\n' \
  "FOUNDRY_PROJECT_ENDPOINT=${PROJECT_ENDPOINT}" \
  "AZURE_AI_MODEL_DEPLOYMENT_NAME=${MODEL_DEPLOYMENT_NAME}" \
  "COSMOS_ENDPOINT=${COSMOS_ENDPOINT}" \
  "COSMOS_DATABASE_NAME=${COSMOS_DATABASE}" \
  "COSMOS_CHECKPOINT_CONTAINER=workflow-checkpoints" \
  "MCP_TOOL_ENDPOINT=${MCP_TOOL_ENDPOINT}" \
  "MCP_FUNCTION_APP_CLIENT_ID=${MCP_ENTRA_CLIENT_ID}" \
  > hosted-agent/.env

echo "Running smoke tests..."
curl --fail --silent --show-error --retry 12 --retry-delay 10 "${APP_URL}/health"
python -m pip install --quiet azure-ai-projects azure-identity
python scripts/smoke-hosted-agent.py \
  --project-endpoint "$PROJECT_ENDPOINT" \
  --agent-name "$HOSTED_AGENT_NAME"

echo
echo "Deployment completed."
echo "Application: ${APP_URL}"
echo "Foundry project: ${PROJECT_ENDPOINT}"
