#!/usr/bin/env bash
set -euo pipefail

RESOURCE_GROUP="${RESOURCE_GROUP:-rg-travel-agent-hosted-demo}"
LOCATION="${LOCATION:-japaneast}"
AZURE_ENV_NAME="${AZURE_ENV_NAME:-travel-agent-local}"
HOSTED_AGENT_NAME="${HOSTED_AGENT_NAME:-travel-request-agent}"
SINGLE_PROMPT_AGENT_NAME="${SINGLE_PROMPT_AGENT_NAME:-travel-request-single-agent}"
SINGLE_PROMPT_EVALUATION_AGENT_NAME="${SINGLE_PROMPT_EVALUATION_AGENT_NAME:-travel-request-single-evaluator}"
MCP_CONNECTION_NAME="${MCP_CONNECTION_NAME:-travel-request-mcp}"
WEB_IQ_CONNECTION_NAME="${WEB_IQ_CONNECTION_NAME:-travel-web-iq}"
WEB_IQ_MCP_ENDPOINT="${WEB_IQ_MCP_ENDPOINT:-https://api.microsoft.ai/v3/mcp}"
CLARIFIER_AGENT_NAME="${CLARIFIER_AGENT_NAME:-travel-request-clarifier}"
PLANNER_AGENT_NAME="${PLANNER_AGENT_NAME:-travel-request-planner}"
POLICY_AGENT_NAME="${POLICY_AGENT_NAME:-travel-request-policy-narrator}"
APPROVAL_AGENT_NAME="${APPROVAL_AGENT_NAME:-travel-request-approval-writer}"
MODEL_DEPLOYMENT_NAME="${MODEL_DEPLOYMENT_NAME:-gpt-5.6-luna}"
EVALUATION_JUDGE_MODEL="${EVALUATION_JUDGE_MODEL:-$MODEL_DEPLOYMENT_NAME}"
RUN_EVALUATION_SMOKE="${RUN_EVALUATION_SMOKE:-false}"
MODEL_CAPACITY="${MODEL_CAPACITY:-1000}"
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

azd extension install azure.ai.projects
azd extension install azure.ai.agents
azd extension install azure.ai.connections
if ! azd auth login --check-status >/dev/null 2>&1; then
  azd auth login
fi

DEPLOY_WORK_ROOT=".deploy-tmp"
DEPLOY_WORK_DIR="${DEPLOY_WORK_ROOT}/run-$(date -u +%Y%m%d%H%M%S)-$$"
DEPLOY_DIR="${DEPLOY_WORK_DIR}/mcp-deploy"
DEPLOY_ZIP="${DEPLOY_WORK_DIR}/mcp-deploy.zip"
PROMPT_AGENT_VERSIONS_FILE="${DEPLOY_WORK_DIR}/prompt-agent-versions.json"
mkdir -p "$DEPLOY_DIR"
cleanup() {
  rm -rf "$DEPLOY_WORK_DIR"
  rmdir "$DEPLOY_WORK_ROOT" 2>/dev/null || true
}
trap cleanup EXIT

SUBSCRIPTION_ID="${AZURE_SUBSCRIPTION_ID:-$(az account show --query id --output tsv)}"
az account set --subscription "$SUBSCRIPTION_ID"
ENTRA_TENANT_ID=$(az account show --query tenantId --output tsv)

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
PROJECT_RESOURCE_ID=$(jq -r '.projectResourceId.value' <<<"$DEPLOY_OUTPUT")
APPINSIGHTS_CONNECTION_STRING=$(jq -r '.appInsightsConnectionString.value' <<<"$DEPLOY_OUTPUT")
APP_INSIGHTS_RESOURCE_ID=$(jq -r '.appInsightsResourceId.value' <<<"$DEPLOY_OUTPUT")
ACR_NAME=$(jq -r '.acrName.value' <<<"$DEPLOY_OUTPUT")
ACR_LOGIN_SERVER=$(jq -r '.acrLoginServer.value' <<<"$DEPLOY_OUTPUT")
COSMOS_ENDPOINT=$(jq -r '.cosmosEndpoint.value' <<<"$DEPLOY_OUTPUT")
COSMOS_ACCOUNT_NAME=$(jq -r '.cosmosAccountName.value' <<<"$DEPLOY_OUTPUT")
COSMOS_DATABASE=$(jq -r '.cosmosDatabaseName.value' <<<"$DEPLOY_OUTPUT")
COSMOS_EVALUATION_CASE_CONTAINER=$(jq -r '.cosmosEvaluationCaseContainerName.value' <<<"$DEPLOY_OUTPUT")
COSMOS_EVALUATION_RUN_CONTAINER=$(jq -r '.cosmosEvaluationRunContainerName.value' <<<"$DEPLOY_OUTPUT")
COSMOS_EVALUATION_RESULT_CONTAINER=$(jq -r '.cosmosEvaluationResultContainerName.value' <<<"$DEPLOY_OUTPUT")
APP_NAME=$(jq -r '.appName.value' <<<"$DEPLOY_OUTPUT")
APP_URL=$(jq -r '.appUrl.value' <<<"$DEPLOY_OUTPUT")
MCP_TOOL_ENDPOINT=$(jq -r '.mcpEndpoint.value' <<<"$DEPLOY_OUTPUT")
FUNCTION_APP_NAME=$(jq -r '.functionAppName.value' <<<"$DEPLOY_OUTPUT")
FUNCTION_STORAGE_ACCOUNT=$(jq -r '.funcStorageAccountName.value' <<<"$DEPLOY_OUTPUT")

echo "Registering the Container Apps Easy Auth callback..."
az ad app update \
  --id "$WEB_ENTRA_CLIENT_ID" \
  --identifier-uris "api://${WEB_ENTRA_CLIENT_ID}" \
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
  "$PROJECT_RESOURCE_ID"

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

echo "Configuring Prompt Agent MCP connection..."
MCP_CONNECTION_JSON=$(azd ai connection create "$MCP_CONNECTION_NAME" \
  --project-endpoint "$PROJECT_ENDPOINT" \
  --kind remote-tool \
  --target "$MCP_TOOL_ENDPOINT" \
  --auth-type project-managed-identity \
  --audience "api://${MCP_ENTRA_CLIENT_ID}" \
  --force \
  --output json \
  --no-prompt)
MCP_CONNECTION_ID=$(jq -er \
  '.id // .connectionId // .name' \
  <<<"$MCP_CONNECTION_JSON")

echo "Configuring Web IQ MCP connection..."
if [[ -n "${WEBIQ_API_KEY:-}" ]]; then
  WEB_IQ_CONNECTION_JSON=$(azd ai connection create "$WEB_IQ_CONNECTION_NAME" \
    --project-endpoint "$PROJECT_ENDPOINT" \
    --kind remote-tool \
    --target "$WEB_IQ_MCP_ENDPOINT" \
    --auth-type custom-keys \
    --custom-key "x-apikey=${WEBIQ_API_KEY}" \
    --force \
    --output json \
    --no-prompt)
else
  WEB_IQ_CONNECTION_JSON=$(azd ai connection show "$WEB_IQ_CONNECTION_NAME" \
    --project-endpoint "$PROJECT_ENDPOINT" \
    --output json \
    --no-prompt)
fi
WEB_IQ_CONNECTION_ID=$(jq -er \
  '.id // .connectionId // .name' \
  <<<"$WEB_IQ_CONNECTION_JSON")

echo "Synchronizing versioned Prompt Agents..."
python -m pip install \
  --quiet \
  "azure-ai-projects>=2.3.0,<2.4.0" \
  "azure-identity>=1.19.0"
for attempt in {1..12}; do
  if python scripts/deploy-prompt-agents.py \
    --project-endpoint "$PROJECT_ENDPOINT" \
    --model "$MODEL_DEPLOYMENT_NAME" \
    --mcp-connection-id "$MCP_CONNECTION_ID" \
    --mcp-server-url "$MCP_TOOL_ENDPOINT" \
    --web-iq-connection-id "$WEB_IQ_CONNECTION_ID" \
    --web-iq-server-url "$WEB_IQ_MCP_ENDPOINT" \
    --output "$PROMPT_AGENT_VERSIONS_FILE" \
    >/dev/null; then
    break
  fi
  if [[ "$attempt" -eq 12 ]]; then
    echo "Foundry RBAC did not become effective in time." >&2
    exit 1
  fi
  sleep 10
done

resolve_prompt_agent_version() {
  local agent_name="$1"
  local version
  version=$(jq -er --arg name "$agent_name" '.[$name]' "$PROMPT_AGENT_VERSIONS_FILE")
  if [[ "$version" == "latest" ]]; then
    echo "${agent_name} resolved to mutable version 'latest'." >&2
    exit 1
  fi
  printf '%s' "$version"
}

CLARIFIER_AGENT_VERSION=$(resolve_prompt_agent_version "$CLARIFIER_AGENT_NAME")
PLANNER_AGENT_VERSION=$(resolve_prompt_agent_version "$PLANNER_AGENT_NAME")
POLICY_AGENT_VERSION=$(resolve_prompt_agent_version "$POLICY_AGENT_NAME")
APPROVAL_AGENT_VERSION=$(resolve_prompt_agent_version "$APPROVAL_AGENT_NAME")
SINGLE_PROMPT_AGENT_VERSION=$(resolve_prompt_agent_version "$SINGLE_PROMPT_AGENT_NAME")
SINGLE_PROMPT_EVALUATION_AGENT_VERSION=$(resolve_prompt_agent_version "$SINGLE_PROMPT_EVALUATION_AGENT_NAME")

echo "Packaging and deploying MCP Functions..."
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

echo "Deploying the Foundry Hosted Agent..."
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
azd env set AZURE_AI_PROJECT_ID "$PROJECT_RESOURCE_ID"
azd env set AZURE_AI_PROJECT_ENDPOINT "$PROJECT_ENDPOINT"
azd env set AZURE_CONTAINER_REGISTRY_ENDPOINT "$ACR_LOGIN_SERVER"
azd env set AZURE_CONTAINER_REGISTRY_RESOURCE_ID "$acr_id"
azd env set FOUNDRY_PROJECT_ENDPOINT "$PROJECT_ENDPOINT"
azd env set AZURE_AI_MODEL_DEPLOYMENT_NAME "$MODEL_DEPLOYMENT_NAME"
azd env set CLARIFIER_AGENT_NAME "$CLARIFIER_AGENT_NAME"
azd env set CLARIFIER_AGENT_VERSION "$CLARIFIER_AGENT_VERSION"
azd env set PLANNER_AGENT_NAME "$PLANNER_AGENT_NAME"
azd env set PLANNER_AGENT_VERSION "$PLANNER_AGENT_VERSION"
azd env set POLICY_AGENT_NAME "$POLICY_AGENT_NAME"
azd env set POLICY_AGENT_VERSION "$POLICY_AGENT_VERSION"
azd env set APPROVAL_AGENT_NAME "$APPROVAL_AGENT_NAME"
azd env set APPROVAL_AGENT_VERSION "$APPROVAL_AGENT_VERSION"
azd env set MCP_TOOL_ENDPOINT "$MCP_TOOL_ENDPOINT"
azd env set MCP_FUNCTION_APP_CLIENT_ID "$MCP_ENTRA_CLIENT_ID"
azd env set COSMOS_ENDPOINT "$COSMOS_ENDPOINT"
azd env set COSMOS_DATABASE_NAME "$COSMOS_DATABASE"
azd env set COSMOS_CHECKPOINT_CONTAINER "workflow-checkpoints"
azd deploy "$HOSTED_AGENT_NAME" --no-prompt

HOSTED_AGENT_VERSION=""
hosted_agent_status=""
for attempt in {1..30}; do
  agent_json=$(azd ai agent show "$HOSTED_AGENT_NAME" \
    --output json \
    --no-prompt 2>/dev/null || true)
  HOSTED_AGENT_VERSION=$(jq -r '.version // empty' <<<"$agent_json")
  hosted_agent_status=$(jq -r '.status // empty' <<<"$agent_json")
  if [[ -n "$HOSTED_AGENT_VERSION" && "$hosted_agent_status" == "active" ]]; then
    break
  fi
  if [[ "$hosted_agent_status" == "failed" ]]; then
    echo "Hosted Agent version ${HOSTED_AGENT_VERSION} failed to activate." >&2
    exit 1
  fi
  sleep 10
done
if [[ -z "$HOSTED_AGENT_VERSION" \
  || "$HOSTED_AGENT_VERSION" == "latest" \
  || "$hosted_agent_status" != "active" ]]; then
  echo "Could not resolve an immutable Hosted Agent version." >&2
  exit 1
fi

echo "Building and deploying the authenticated BFF..."
GIT_REVISION="$(git rev-parse --short HEAD 2>/dev/null || true)"
if [[ -z "$GIT_REVISION" ]]; then
  GIT_REVISION="$(date -u +%Y%m%d%H%M%S)"
fi
IMAGE_TAG="${IMAGE_NAME}:${GIT_REVISION}"
BFF_BUILD_CONTEXT="${DEPLOY_WORK_DIR}/app-build"
mkdir -p "${BFF_BUILD_CONTEXT}/backend" "${BFF_BUILD_CONTEXT}/frontend"
cp app/Dockerfile "$BFF_BUILD_CONTEXT/"
cp app/backend/requirements.txt "${BFF_BUILD_CONTEXT}/backend/"
cp -R app/backend/app app/backend/config "${BFF_BUILD_CONTEXT}/backend/"
cp app/frontend/package.json \
  app/frontend/package-lock.json \
  app/frontend/next.config.js \
  app/frontend/postcss.config.mjs \
  app/frontend/tsconfig.json \
  app/frontend/next-env.d.ts \
  "${BFF_BUILD_CONTEXT}/frontend/"
cp -R app/frontend/src "${BFF_BUILD_CONTEXT}/frontend/"
cat infra/bff-runtime-constraints.txt >> "${BFF_BUILD_CONTEXT}/backend/requirements.txt"
az acr build \
  --registry "$ACR_NAME" \
  --image "$IMAGE_TAG" \
  --file "${BFF_BUILD_CONTEXT}/Dockerfile" \
  "$BFF_BUILD_CONTEXT" \
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
DEPLOYED_REVISION=$(az containerapp update \
  --name "$APP_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --image "${ACR_LOGIN_SERVER}/${IMAGE_TAG}" \
  --set-env-vars \
    "AZURE_AI_MODEL_DEPLOYMENT_NAME=$MODEL_DEPLOYMENT_NAME" \
    "HOSTED_AGENT_NAME=$HOSTED_AGENT_NAME" \
    "HOSTED_AGENT_VERSION=$HOSTED_AGENT_VERSION" \
    "SINGLE_PROMPT_AGENT_NAME=$SINGLE_PROMPT_AGENT_NAME" \
    "SINGLE_PROMPT_AGENT_VERSION=$SINGLE_PROMPT_AGENT_VERSION" \
    "SINGLE_PROMPT_EVALUATION_AGENT_NAME=$SINGLE_PROMPT_EVALUATION_AGENT_NAME" \
    "SINGLE_PROMPT_EVALUATION_AGENT_VERSION=$SINGLE_PROMPT_EVALUATION_AGENT_VERSION" \
    "EVALUATION_JUDGE_MODEL=$EVALUATION_JUDGE_MODEL" \
    "ENTRA_TENANT_ID=$ENTRA_TENANT_ID" \
    "COSMOS_EVALUATION_CASE_CONTAINER=$COSMOS_EVALUATION_CASE_CONTAINER" \
    "COSMOS_EVALUATION_RUN_CONTAINER=$COSMOS_EVALUATION_RUN_CONTAINER" \
    "COSMOS_EVALUATION_RESULT_CONTAINER=$COSMOS_EVALUATION_RESULT_CONTAINER" \
  --query properties.latestRevisionName \
  --output tsv)

echo "Waiting for Container App revision ${DEPLOYED_REVISION}..."
for attempt in {1..30}; do
  revision_health=$(az containerapp revision show \
    --name "$APP_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --revision "$DEPLOYED_REVISION" \
    --query properties.healthState \
    --output tsv)
  latest_ready_revision=$(az containerapp show \
    --name "$APP_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --query properties.latestReadyRevisionName \
    --output tsv)
  if [[ "$revision_health" == "Healthy" \
    && "$latest_ready_revision" == "$DEPLOYED_REVISION" ]]; then
    break
  fi
  if [[ "$attempt" -eq 30 ]]; then
    echo "Container App revision ${DEPLOYED_REVISION} did not become healthy." >&2
    az containerapp revision show \
      --name "$APP_NAME" \
      --resource-group "$RESOURCE_GROUP" \
      --revision "$DEPLOYED_REVISION" \
      --query '{health:properties.healthState,runningState:properties.runningState,details:properties.runningStateDetails}' \
      --output json >&2
    exit 1
  fi
  sleep 10
done

AZURE_SUBSCRIPTION_ID="$SUBSCRIPTION_ID" \
AZURE_AI_PROJECT_ENDPOINT="$PROJECT_ENDPOINT" \
FOUNDRY_ACCOUNT_RESOURCE_ID="$ai_account_id" \
FOUNDRY_PROJECT_RESOURCE_ID="$PROJECT_RESOURCE_ID" \
APP_INSIGHTS_RESOURCE_ID="$APP_INSIGHTS_RESOURCE_ID" \
RESOURCE_GROUP="$RESOURCE_GROUP" \
FOUNDRY_ACCOUNT_NAME="$ACCOUNT_NAME" \
FOUNDRY_PROJECT_NAME="$PROJECT_NAME" \
COSMOS_ACCOUNT_NAME="$COSMOS_ACCOUNT_NAME" \
CONTAINER_APP_NAME="$APP_NAME" \
FUNCTION_APP_NAME="$FUNCTION_APP_NAME" \
HOSTED_AGENT_NAME="$HOSTED_AGENT_NAME" \
HOSTED_AGENT_VERSION="$HOSTED_AGENT_VERSION" \
  bash scripts/configure-hosted-agent.sh

printf '%s\n' \
  "AZURE_AI_PROJECT_ENDPOINT=${PROJECT_ENDPOINT}" \
  "AZURE_AI_MODEL_DEPLOYMENT_NAME=${MODEL_DEPLOYMENT_NAME}" \
  "HOSTED_AGENT_NAME=${HOSTED_AGENT_NAME}" \
  "HOSTED_AGENT_VERSION=${HOSTED_AGENT_VERSION}" \
  "SINGLE_PROMPT_AGENT_NAME=${SINGLE_PROMPT_AGENT_NAME}" \
  "SINGLE_PROMPT_AGENT_VERSION=${SINGLE_PROMPT_AGENT_VERSION}" \
  "SINGLE_PROMPT_EVALUATION_AGENT_NAME=${SINGLE_PROMPT_EVALUATION_AGENT_NAME}" \
  "SINGLE_PROMPT_EVALUATION_AGENT_VERSION=${SINGLE_PROMPT_EVALUATION_AGENT_VERSION}" \
  "EVALUATION_JUDGE_MODEL=${EVALUATION_JUDGE_MODEL}" \
  "COSMOS_ENDPOINT=${COSMOS_ENDPOINT}" \
  "COSMOS_DATABASE=${COSMOS_DATABASE}" \
  "COSMOS_EVALUATION_CASE_CONTAINER=${COSMOS_EVALUATION_CASE_CONTAINER}" \
  "COSMOS_EVALUATION_RUN_CONTAINER=${COSMOS_EVALUATION_RUN_CONTAINER}" \
  "COSMOS_EVALUATION_RESULT_CONTAINER=${COSMOS_EVALUATION_RESULT_CONTAINER}" \
  "APPLICATIONINSIGHTS_CONNECTION_STRING=${APPINSIGHTS_CONNECTION_STRING}" \
  "ENTRA_TENANT_ID=${ENTRA_TENANT_ID}" \
  "ENTRA_CLIENT_ID=${WEB_ENTRA_CLIENT_ID}" \
  > .env
cp .env app/backend/.env
printf '%s\n' \
  "FOUNDRY_PROJECT_ENDPOINT=${PROJECT_ENDPOINT}" \
  "AZURE_AI_MODEL_DEPLOYMENT_NAME=${MODEL_DEPLOYMENT_NAME}" \
  "CLARIFIER_AGENT_NAME=${CLARIFIER_AGENT_NAME}" \
  "CLARIFIER_AGENT_VERSION=${CLARIFIER_AGENT_VERSION}" \
  "PLANNER_AGENT_NAME=${PLANNER_AGENT_NAME}" \
  "PLANNER_AGENT_VERSION=${PLANNER_AGENT_VERSION}" \
  "POLICY_AGENT_NAME=${POLICY_AGENT_NAME}" \
  "POLICY_AGENT_VERSION=${POLICY_AGENT_VERSION}" \
  "APPROVAL_AGENT_NAME=${APPROVAL_AGENT_NAME}" \
  "APPROVAL_AGENT_VERSION=${APPROVAL_AGENT_VERSION}" \
  "COSMOS_ENDPOINT=${COSMOS_ENDPOINT}" \
  "COSMOS_DATABASE_NAME=${COSMOS_DATABASE}" \
  "COSMOS_CHECKPOINT_CONTAINER=workflow-checkpoints" \
  "MCP_TOOL_ENDPOINT=${MCP_TOOL_ENDPOINT}" \
  "MCP_FUNCTION_APP_CLIENT_ID=${MCP_ENTRA_CLIENT_ID}" \
  > hosted-agent/.env

echo "Running smoke tests..."
curl --fail --silent --show-error --retry 12 --retry-delay 10 "${APP_URL}/health"

if [[ -f app/backend/app/routers/evaluations.py \
  && "$RUN_EVALUATION_SMOKE" == "true" ]]; then
  access_token=$(az account get-access-token \
    --resource "api://${WEB_ENTRA_CLIENT_ID}" \
    --query accessToken \
    --output tsv)
  seed_response="${DEPLOY_WORK_DIR}/evaluation-seed-response.json"
  curl --fail --silent --show-error \
    --request POST \
    --header "Authorization: Bearer ${access_token}" \
    --header "Content-Type: application/json" \
    --data '{"seed_default":true,"overwrite":false}' \
    "$APP_URL/api/evaluations/cases/import" \
    --output "$seed_response"

  run_response="${DEPLOY_WORK_DIR}/evaluation-smoke-run.json"
  curl --fail --silent --show-error \
    --request POST \
    --header "Authorization: Bearer ${access_token}" \
    --header "Content-Type: application/json" \
    --data "{
      \"name\": \"local-deployment-smoke-$(date -u +%Y%m%d%H%M%S)\",
      \"dataset_id\": \"travel-request-v1\",
      \"case_ids\": [
        \"case-standard-tokyo-osaka\",
        \"case-standard-osaka-fukuoka\"
      ]
    }" \
    "$APP_URL/api/evaluations/runs" \
    --output "$run_response"
  run_id=$(jq -er '.id' "$run_response")

  run_status=""
  for attempt in {1..120}; do
    curl --fail --silent --show-error \
      --header "Authorization: Bearer ${access_token}" \
      "$APP_URL/api/evaluations/runs/${run_id}" \
      --output "$run_response"
    run_status=$(jq -r '.status // empty' "$run_response")
    if jq -e '
      [.scenario_runs[]?.status // empty]
      | any(. == "failed" or . == "canceled")
    ' "$run_response" >/dev/null; then
      jq '{id, status, error, scenario_runs}' "$run_response" >&2
      exit 1
    fi
    case "$run_status" in
      completed)
        break
        ;;
      failed|error|cancelled|canceled|partial_failure|partially_completed)
        jq '{id, status, error, scenario_runs}' "$run_response" >&2
        exit 1
        ;;
    esac
    if [[ "$attempt" -eq 120 ]]; then
      echo "Paired evaluation smoke test timed out." >&2
      exit 1
    fi
    sleep 15
  done

  jq -e \
    --arg hosted_version "$HOSTED_AGENT_VERSION" \
    --arg single_version "$SINGLE_PROMPT_EVALUATION_AGENT_VERSION" \
    '.scenario_runs.agent_framework_workflow.agent_version == $hosted_version
     and .scenario_runs.single_prompt_agent.agent_version == $single_version' \
    "$run_response" >/dev/null

  results_response="${DEPLOY_WORK_DIR}/evaluation-smoke-results.json"
  curl --fail --silent --show-error \
    --header "Authorization: Bearer ${access_token}" \
    "$APP_URL/api/evaluations/runs/${run_id}/results" \
    --output "$results_response"
  jq -e '
    type == "array"
    and length == 2
    and all(.[];
      .scenarios.agent_framework_workflow != null
      and .scenarios.single_prompt_agent != null
      and ((.scenarios.agent_framework_workflow.error // "") == "")
      and ((.scenarios.single_prompt_agent.error // "") == "")
    )
  ' "$results_response" >/dev/null
elif [[ -f app/backend/app/routers/evaluations.py ]]; then
  echo "Paired evaluation smoke test disabled; set RUN_EVALUATION_SMOKE=true to run it."
else
  echo "Evaluation backend contract not present; skipping paired evaluation smoke."
fi

python scripts/smoke-hosted-agent.py \
  --project-endpoint "$PROJECT_ENDPOINT" \
  --agent-name "$HOSTED_AGENT_NAME"

echo
echo "Deployment completed."
echo "Application: ${APP_URL}"
echo "Foundry project: ${PROJECT_ENDPOINT}"
