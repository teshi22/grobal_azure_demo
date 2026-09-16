#!/usr/bin/env bash
set -euo pipefail

: "${AZURE_AI_PROJECT_ENDPOINT:?AZURE_AI_PROJECT_ENDPOINT is required}"
: "${HOSTED_AGENT_NAME:=travel-request-agent}"
: "${FOUNDRY_ACCOUNT_RESOURCE_ID:?FOUNDRY_ACCOUNT_RESOURCE_ID is required}"
: "${FOUNDRY_PROJECT_RESOURCE_ID:?FOUNDRY_PROJECT_RESOURCE_ID is required}"
: "${APP_INSIGHTS_RESOURCE_ID:?APP_INSIGHTS_RESOURCE_ID is required}"
: "${RESOURCE_GROUP:?RESOURCE_GROUP is required}"
: "${COSMOS_ACCOUNT_NAME:?COSMOS_ACCOUNT_NAME is required}"
: "${CONTAINER_APP_NAME:?CONTAINER_APP_NAME is required}"
: "${FUNCTION_APP_NAME:?FUNCTION_APP_NAME is required}"

agent_principal_id=""
for _ in $(seq 1 30); do
  agent_principal_id=$(az rest \
    --method GET \
    --url "${AZURE_AI_PROJECT_ENDPOINT}/agents/${HOSTED_AGENT_NAME}?api-version=v1" \
    --resource "https://ai.azure.com" \
    --query "instance_identity.principal_id" \
    --output tsv 2>/dev/null || true)
  if [[ -n "$agent_principal_id" ]]; then
    break
  fi
  sleep 10
done

if [[ -z "$agent_principal_id" ]]; then
  echo "Hosted Agent instance identity was not available." >&2
  exit 1
fi

if [[ -z "$(az role assignment list \
  --assignee-object-id "$agent_principal_id" \
  --scope "$FOUNDRY_PROJECT_RESOURCE_ID" \
  --role "Foundry Agent Consumer" \
  --query '[0].id' \
  --output tsv)" ]]; then
  az role assignment create \
    --assignee-object-id "$agent_principal_id" \
    --assignee-principal-type ServicePrincipal \
    --role "Foundry Agent Consumer" \
    --scope "$FOUNDRY_PROJECT_RESOURCE_ID" \
    --output none
fi

if [[ -z "$(az role assignment list \
  --assignee-object-id "$agent_principal_id" \
  --scope "$APP_INSIGHTS_RESOURCE_ID" \
  --role "Monitoring Metrics Publisher" \
  --query '[0].id' \
  --output tsv)" ]]; then
  az role assignment create \
    --assignee-object-id "$agent_principal_id" \
    --assignee-principal-type ServicePrincipal \
    --role "Monitoring Metrics Publisher" \
    --scope "$APP_INSIGHTS_RESOURCE_ID" \
    --output none
fi

agent_client_id=$(az ad sp show \
  --id "$agent_principal_id" \
  --query appId \
  --output tsv)

bff_principal_id=$(az containerapp show \
  --resource-group "$RESOURCE_GROUP" \
  --name "$CONTAINER_APP_NAME" \
  --query identity.principalId \
  --output tsv)

project_principal_id=$(az rest \
  --method GET \
  --url "https://management.azure.com${FOUNDRY_PROJECT_RESOURCE_ID}?api-version=2025-04-01-preview" \
  --query identity.principalId \
  --output tsv)
if [[ -z "$project_principal_id" ]]; then
  echo "Foundry project managed identity was not available." >&2
  exit 1
fi
project_client_id=$(az ad sp show \
  --id "$project_principal_id" \
  --query appId \
  --output tsv)

impersonation_role_name="Foundry Agent User Identity Impersonation ${FOUNDRY_ACCOUNT_RESOURCE_ID##*/}"
if [[ -z "$(az role definition list \
  --name "$impersonation_role_name" \
  --query '[0].id' \
  --output tsv)" ]]; then
  impersonation_role=$(jq -n \
    --arg name "$impersonation_role_name" \
    --arg scope "$FOUNDRY_ACCOUNT_RESOURCE_ID" \
    '{
      Name: $name,
      IsCustom: true,
      Description: "Allows the BFF to scope Hosted Agent sessions to authenticated users.",
      Actions: [],
      NotActions: [],
      DataActions: [
        "Microsoft.CognitiveServices/accounts/AIServices/agents/endpoints/UserIdentityImpersonation/action"
      ],
      NotDataActions: [],
      AssignableScopes: [$scope]
    }')
  az role definition create \
    --role-definition "$impersonation_role" \
    --output none
fi

if [[ -z "$(az role assignment list \
  --assignee-object-id "$bff_principal_id" \
  --scope "$FOUNDRY_PROJECT_RESOURCE_ID" \
  --role "$impersonation_role_name" \
  --query '[0].id' \
  --output tsv)" ]]; then
  az role assignment create \
    --assignee-object-id "$bff_principal_id" \
    --assignee-principal-type ServicePrincipal \
    --role "$impersonation_role_name" \
    --scope "$FOUNDRY_PROJECT_RESOURCE_ID" \
    --output none
fi

cosmos_scope=$(az cosmosdb show \
  --resource-group "$RESOURCE_GROUP" \
  --name "$COSMOS_ACCOUNT_NAME" \
  --query id \
  --output tsv)
cosmos_role_id="00000000-0000-0000-0000-000000000002"
existing_cosmos_assignment=$(az cosmosdb sql role assignment list \
  --resource-group "$RESOURCE_GROUP" \
  --account-name "$COSMOS_ACCOUNT_NAME" \
  --output json \
  | jq -r \
    --arg principal_id "$agent_principal_id" \
    --arg role_id "$cosmos_role_id" \
    --arg scope "$cosmos_scope" \
    '[
      .[]
      | select(.principalId == $principal_id)
      | select(.roleDefinitionId | endswith($role_id))
      | select(.scope == "/" or .scope == $scope)
    ][0].id // empty')
if [[ -z "$existing_cosmos_assignment" ]]; then
  az cosmosdb sql role assignment create \
    --resource-group "$RESOURCE_GROUP" \
    --account-name "$COSMOS_ACCOUNT_NAME" \
    --role-definition-id "$cosmos_role_id" \
    --principal-id "$agent_principal_id" \
    --scope "$cosmos_scope" \
    --output none
fi

function_auth_id=$(az functionapp show \
  --resource-group "$RESOURCE_GROUP" \
  --name "$FUNCTION_APP_NAME" \
  --query id \
  --output tsv)/config/authsettingsV2
auth_config=$(az rest \
  --method GET \
  --url "https://management.azure.com${function_auth_id}?api-version=2024-04-01")
auth_config=$(jq \
  --arg agent_client_id "$agent_client_id" \
  --arg project_client_id "$project_client_id" \
  '.properties.identityProviders.azureActiveDirectory.validation.defaultAuthorizationPolicy.allowedApplications = ([$agent_client_id, $project_client_id] | unique)
   | {properties: .properties}' \
  <<<"$auth_config")
az rest \
  --method PUT \
  --url "https://management.azure.com${function_auth_id}?api-version=2024-04-01" \
  --body "$auth_config" \
  --output none

echo "Configured Hosted Agent identity ${agent_principal_id}, Foundry project identity ${project_principal_id}, and BFF impersonation access."
