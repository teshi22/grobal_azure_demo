// =============================================================================
// 出張申請エージェント - Microsoft Foundry インフラストラクチャ
// =============================================================================

@description('AI Services アカウント名のプレフィックス')
@maxLength(9)
param aiServicesName string = 'travel'

@description('プロジェクト名')
param projectName string = 'travel-agent'

@description('プロジェクトの説明')
param projectDescription string = 'AI Travel Request Agent Demo'

@description('プロジェクトの表示名')
param projectDisplayName string = 'Travel Request Agent'

@description('Foundry リソースのデプロイリージョン')
param location string = 'swedencentral'

@description('Foundry 以外のリソースのデプロイリージョン')
param secondaryLocation string = 'japaneast'

@description('GPT モデル名')
param modelName string = 'gpt-5.4'

@description('モデルフォーマット')
param modelFormat string = 'OpenAI'

@description('モデルバージョン')
param modelVersion string = '2026-03-05'

@description('モデル SKU')
param modelSkuName string = 'GlobalStandard'

@description('モデルキャパシティ (TPM)')
param modelCapacity int = 30

@description('MCP Functions 用 Entra ID アプリ登録のクライアント ID')
param mcpEntraClientId string = ''

// ユニークサフィックス生成 (リソースグループに対して決定論的)
var uniqueSuffix = substring(uniqueString(resourceGroup().id), 0, 4)
var accountName = toLower('${aiServicesName}${uniqueSuffix}')

// =============================================================================
// 1. AI Services アカウント
// =============================================================================
module aiAccount 'modules/ai-account.bicep' = {
  name: 'ai-account-${uniqueSuffix}'
  params: {
    accountName: accountName
    location: location
    modelName: modelName
    modelFormat: modelFormat
    modelVersion: modelVersion
    modelSkuName: modelSkuName
    modelCapacity: modelCapacity
  }
}

// =============================================================================
// 2. プロジェクト
// =============================================================================
module aiProject 'modules/ai-project.bicep' = {
  name: 'ai-project-${uniqueSuffix}'
  params: {
    accountName: aiAccount.outputs.accountName
    projectName: projectName
    description: projectDescription
    displayName: projectDisplayName
    location: location
  }
}

// =============================================================================
// 3. Bing Search (Grounding 用)
// =============================================================================
module bingSearch 'modules/bing-search.bicep' = {
  name: 'bing-search-${uniqueSuffix}'
  params: {
    accountName: aiAccount.outputs.accountName
  }
}

// =============================================================================
// 4. Application Insights (トレース用)
// =============================================================================
module appInsights 'modules/app-insights.bicep' = {
  name: 'app-insights-${uniqueSuffix}'
  params: {
    appInsightsName: '${accountName}-insights'
    location: secondaryLocation
  }
}

// =============================================================================
// 5. Azure Container Registry
// =============================================================================
var acrName = toLower('${aiServicesName}acr${uniqueSuffix}')

module acr 'modules/acr.bicep' = {
  name: 'acr-${uniqueSuffix}'
  params: {
    acrName: acrName
    location: secondaryLocation
  }
}

// =============================================================================
// 6. Cosmos DB (チェックポイント + イベントストア + 会話メタデータ)
// =============================================================================
var cosmosAccountName = toLower('${aiServicesName}cosmos${uniqueSuffix}')

module cosmosDb 'modules/cosmos-db.bicep' = {
  name: 'cosmos-db-${uniqueSuffix}'
  params: {
    accountName: cosmosAccountName
    location: secondaryLocation
    // RBAC は Container Apps デプロイ後に別途設定 (循環依存回避)
  }
}

// =============================================================================
// 7. Container Apps (Frontend + Backend)
// =============================================================================
var envName = toLower('${aiServicesName}env${uniqueSuffix}')

module containerApps 'modules/container-apps.bicep' = {
  name: 'container-apps-${uniqueSuffix}'
  params: {
    envName: envName
    location: secondaryLocation
    acrLoginServer: acr.outputs.acrLoginServer
    cosmosEndpoint: cosmosDb.outputs.accountEndpoint
    aiProjectEndpoint: '${aiAccount.outputs.endpoint}api/projects/${projectName}'
    mcpToolEndpoint: functions.outputs.mcpEndpoint
    mcpFunctionAppClientId: mcpEntraClientId
    appInsightsConnectionString: appInsights.outputs.connectionString
    logAnalyticsCustomerId: appInsights.outputs.logAnalyticsCustomerId
    logAnalyticsSharedKey: appInsights.outputs.logAnalyticsSharedKey
  }
}

// Cosmos DB RBAC: アプリの Managed Identity にデータ投稿者ロールを付与
resource cosmosAccountRef 'Microsoft.DocumentDB/databaseAccounts@2024-11-15' existing = {
  name: cosmosAccountName
}

var cosmosDataContributorRoleId = '00000000-0000-0000-0000-000000000002'

resource appCosmosRbac 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-11-15' = {
  parent: cosmosAccountRef
  name: guid(cosmosAccountRef.id, envName, 'cosmos-data-contributor')
  properties: {
    roleDefinitionId: '${cosmosAccountRef.id}/sqlRoleDefinitions/${cosmosDataContributorRoleId}'
    principalId: containerApps.outputs.appPrincipalId
    scope: cosmosAccountRef.id
  }
}

// =============================================================================
// 8. Azure Functions (MCP ツール)
// =============================================================================
var functionAppName = toLower('${aiServicesName}func${uniqueSuffix}')
var funcStorageName = toLower('${aiServicesName}fs${uniqueSuffix}')

module functions 'modules/functions.bicep' = {
  name: 'functions-${uniqueSuffix}'
  params: {
    functionAppName: functionAppName
    storageAccountName: funcStorageName
    location: secondaryLocation
    appInsightsConnectionString: appInsights.outputs.connectionString
    cosmosEndpoint: cosmosDb.outputs.accountEndpoint
    mcpEntraClientId: mcpEntraClientId
  }
}

// Cosmos DB RBAC: Functions の Managed Identity にデータ投稿者ロールを付与
resource funcCosmosRbac 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-11-15' = {
  parent: cosmosAccountRef
  name: guid(cosmosAccountRef.id, functionAppName, 'cosmos-data-contributor')
  properties: {
    roleDefinitionId: '${cosmosAccountRef.id}/sqlRoleDefinitions/${cosmosDataContributorRoleId}'
    principalId: functions.outputs.principalId
    scope: cosmosAccountRef.id
  }
}

// =============================================================================
// Outputs
// =============================================================================
output accountName string = aiAccount.outputs.accountName
output projectName string = aiProject.outputs.projectName
output endpoint string = aiAccount.outputs.endpoint
output projectEndpoint string = '${aiAccount.outputs.endpoint}api/projects/${projectName}'
output bingConnectionName string = bingSearch.outputs.connectionName
output appInsightsConnectionString string = appInsights.outputs.connectionString
output acrName string = acr.outputs.acrName
output acrLoginServer string = acr.outputs.acrLoginServer
output cosmosEndpoint string = cosmosDb.outputs.accountEndpoint
output appUrl string = 'https://${containerApps.outputs.appFqdn}'
output mcpEndpoint string = functions.outputs.mcpEndpoint
output functionAppName string = functions.outputs.functionAppName
output funcStorageAccountName string = functions.outputs.storageAccountName
