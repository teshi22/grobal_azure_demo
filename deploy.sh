#!/bin/bash
set -euo pipefail

# =============================================================================
# 出張申請エージェント - デプロイスクリプト
# =============================================================================

SUBSCRIPTION_ID="5290deef-ab3d-4e26-90bb-2296ecd99c71"
RESOURCE_GROUP="rg-travel-agent-demo"
LOCATION="swedencentral"
SECONDARY_LOCATION="japaneast"

echo "=== 出張申請エージェント デプロイ ==="

# Entra ID アプリ登録 (MCP Functions EasyAuth 用) — 冪等
echo "0. MCP Functions 用 Entra ID アプリ登録..."
MCP_APP_DISPLAY_NAME="travel-mcp-functions"
MCP_ENTRA_CLIENT_ID=$(az ad app list --display-name "$MCP_APP_DISPLAY_NAME" \
    --query "[0].appId" --output tsv 2>/dev/null || echo "")
if [ -z "$MCP_ENTRA_CLIENT_ID" ]; then
  MCP_ENTRA_CLIENT_ID=$(az ad app create --display-name "$MCP_APP_DISPLAY_NAME" \
      --sign-in-audience AzureADMyOrg --query "appId" --output tsv)
  echo "  アプリ登録作成: $MCP_ENTRA_CLIENT_ID"
  # Entra レプリケーション待機
  sleep 10
else
  echo "  既存アプリ登録を使用: $MCP_ENTRA_CLIENT_ID"
fi

# Application ID URI の設定 (トークンの audience として必要)
az ad app update --id "$MCP_ENTRA_CLIENT_ID" \
    --identifier-uris "api://${MCP_ENTRA_CLIENT_ID}" --output none 2>/dev/null || true

# サブスクリプション設定
echo "1. サブスクリプション設定..."
az account set --subscription "$SUBSCRIPTION_ID"

# リソースグループ作成
echo "2. リソースグループ作成: $RESOURCE_GROUP ($LOCATION)..."
az group create --name "$RESOURCE_GROUP" --location "$LOCATION" --output none

# Bicep デプロイ
echo "3. Foundry インフラデプロイ中..."
DEPLOY_OUTPUT=$(az deployment group create \
  --resource-group "$RESOURCE_GROUP" \
  --template-file infra/main.bicep \
  --parameters location="$LOCATION" secondaryLocation="$SECONDARY_LOCATION" mcpEntraClientId="$MCP_ENTRA_CLIENT_ID" \
  --query "properties.outputs" \
  --output json)

# 出力値の取得
ACCOUNT_NAME=$(echo "$DEPLOY_OUTPUT" | jq -r '.accountName.value')
PROJECT_NAME=$(echo "$DEPLOY_OUTPUT" | jq -r '.projectName.value')
ENDPOINT=$(echo "$DEPLOY_OUTPUT" | jq -r '.endpoint.value')
PROJECT_ENDPOINT=$(echo "$DEPLOY_OUTPUT" | jq -r '.projectEndpoint.value')
BING_CONNECTION=$(echo "$DEPLOY_OUTPUT" | jq -r '.bingConnectionName.value')
APPINSIGHTS_CONN=$(echo "$DEPLOY_OUTPUT" | jq -r '.appInsightsConnectionString.value')
MCP_ENDPOINT=$(echo "$DEPLOY_OUTPUT" | jq -r '.mcpEndpoint.value')
FUNC_STORAGE=$(echo "$DEPLOY_OUTPUT" | jq -r '.funcStorageAccountName.value')

echo ""
echo "=== デプロイ完了 ==="
echo "Account Name:     $ACCOUNT_NAME"
echo "Project Name:     $PROJECT_NAME"
echo "Endpoint:         $ENDPOINT"
echo "Project Endpoint: $PROJECT_ENDPOINT"
echo "Bing Connection:  $BING_CONNECTION"
echo "App Insights:     ${APPINSIGHTS_CONN:0:60}..."
echo "MCP Endpoint:     $MCP_ENDPOINT"
echo ""

# 現在のユーザーにロール割り当て
echo "4. ロール割り当て中..."
USER_OBJECT_ID=$(az ad signed-in-user show --query id --output tsv)
ACCOUNT_RESOURCE_ID=$(az cognitiveservices account show \
  --name "$ACCOUNT_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --query id --output tsv)

az role assignment create \
  --assignee "$USER_OBJECT_ID" \
  --role "Azure AI Developer" \
  --scope "$ACCOUNT_RESOURCE_ID" \
  --output none 2>/dev/null || echo "  (ロール割り当て済み、スキップ)"

# Bing connection ID の取得 (プロジェクトスコープ)
echo "5. Bing 接続 ID 取得中..."
ACCOUNT_RESOURCE_ID=$(az cognitiveservices account show \
  --name "$ACCOUNT_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --query id --output tsv)
BING_CONNECTION_ID="${ACCOUNT_RESOURCE_ID}/projects/${PROJECT_NAME}/connections/${BING_CONNECTION}"

# MCP Functions デプロイ
echo "6. MCP Functions デプロイ中..."

# デプロイユーザーに Storage Blob Data Contributor を付与 (allowSharedKeyAccess=false 対応)
FUNC_STORAGE_ID=$(az storage account show --name "$FUNC_STORAGE" --resource-group "$RESOURCE_GROUP" --query id --output tsv)
az role assignment create \
  --assignee "$USER_OBJECT_ID" \
  --role "Storage Blob Data Contributor" \
  --scope "$FUNC_STORAGE_ID" \
  --output none 2>/dev/null || echo "  (Storage ロール割り当て済み、スキップ)"
echo "  RBAC 伝播待機中 (30秒)..."
sleep 30

MCP_DIR="mcp-tools"
DEPLOY_DIR="/tmp/mcp-deploy-$$"
mkdir -p "$DEPLOY_DIR"
cp -r "$MCP_DIR/function_app.py" "$MCP_DIR/host.json" "$MCP_DIR/tools" "$DEPLOY_DIR/"
pip install --quiet -r "$MCP_DIR/requirements.txt" \
    --target "$DEPLOY_DIR/.python_packages/lib/site-packages" \
    --platform manylinux2014_x86_64 --python-version 3.11 --implementation cp --abi cp311 --only-binary=:all:
(cd "$DEPLOY_DIR" && zip -qr /tmp/mcp-deploy.zip .)

az storage blob upload --account-name "$FUNC_STORAGE" --container-name function-releases \
    --file /tmp/mcp-deploy.zip --name mcp-deploy.zip --overwrite --auth-mode login --output none
az functionapp restart --name "$(echo "$DEPLOY_OUTPUT" | jq -r '.functionAppName.value')" \
    --resource-group "$RESOURCE_GROUP" --output none
rm -rf "$DEPLOY_DIR" /tmp/mcp-deploy.zip
echo "  MCP Functions デプロイ完了"

# .env ファイル作成
echo "7. .env ファイル作成中..."
cat > .env << EOF
AZURE_AI_PROJECT_ENDPOINT=${PROJECT_ENDPOINT}
AZURE_AI_MODEL_DEPLOYMENT_NAME=gpt-5.4
BING_CONNECTION_NAME=${BING_CONNECTION}
BING_PROJECT_CONNECTION_ID=${BING_CONNECTION_ID}
APPLICATIONINSIGHTS_CONNECTION_STRING=${APPINSIGHTS_CONN}
MCP_TOOL_ENDPOINT=${MCP_ENDPOINT}
MCP_FUNCTION_APP_CLIENT_ID=${MCP_ENTRA_CLIENT_ID}
EOF

echo ""
echo "=== セットアップ完了 ==="
echo ".env ファイルが作成されました。"
echo ""
echo "次のステップ:"
echo "  pip install -r requirements.txt"
echo "  python scripts/build_agents.py"
