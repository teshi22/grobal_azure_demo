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

@description('デプロイリージョン')
param location string = 'swedencentral'

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

// ユニークサフィックス生成
param deploymentTimestamp string = utcNow('yyyyMMddHHmmss')
var uniqueSuffix = substring(uniqueString('${resourceGroup().id}-${deploymentTimestamp}'), 0, 4)
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
    location: location
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
