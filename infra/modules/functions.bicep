// Azure Functions — MCP ツールホスティング
// Storage Account + Functions App (Python)

@description('リソースのロケーション')
param location string = resourceGroup().location

@description('Functions App 名')
param functionAppName string

@description('Storage Account 名')
param storageAccountName string

@description('Cosmos DB エンドポイント')
param cosmosEndpoint string = ''

@description('Application Insights 接続文字列')
param appInsightsConnectionString string = ''

@description('MCP 用 Entra ID アプリ登録のクライアント ID（EasyAuth 認証用）')
param mcpEntraClientId string = ''

@description('Entra テナント ID')
param entraTenantId string = subscription().tenantId

// Storage Account (Functions 用)
resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageAccountName
  location: location
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  tags: {
    SecurityControl: 'Ignore'
  }
  properties: {
    supportsHttpsTrafficOnly: true
    minimumTlsVersion: 'TLS1_2'
    publicNetworkAccess: 'Enabled'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
  }
}

// Blob コンテナ (デプロイパッケージ用)
resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
}

resource releaseContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: 'function-releases'
  properties: {
    publicAccess: 'None'
  }
}

// Storage RBAC: Functions MI に Blob Data Owner を付与
var storageBlobDataOwnerRoleId = 'b7e6dc6d-f1e8-4753-8033-0f276bb0955b'

resource funcStorageBlobOwner 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, functionApp.id, storageBlobDataOwnerRoleId)
  scope: storage
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataOwnerRoleId)
    principalId: functionApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// Storage Queue Data Contributor (Functions ランタイム内部通信用)
var storageQueueDataContributorRoleId = '974c5e8b-45b9-4653-ba55-5f855dd0fb88'

resource funcStorageQueueContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, functionApp.id, storageQueueDataContributorRoleId)
  scope: storage
  properties: {
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      storageQueueDataContributorRoleId
    )
    principalId: functionApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// Storage Table Data Contributor (Functions ランタイム内部通信用)
var storageTableDataContributorRoleId = '0a9a7e1f-b9d0-4cc4-a60d-0319b160aaa3'

resource funcStorageTableContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, functionApp.id, storageTableDataContributorRoleId)
  scope: storage
  properties: {
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      storageTableDataContributorRoleId
    )
    principalId: functionApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// App Service Plan (Consumption)
resource plan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: '${functionAppName}-plan'
  location: location
  sku: {
    name: 'Y1'
    tier: 'Dynamic'
  }
  properties: {
    reserved: true // Linux
  }
}

// Functions App
resource functionApp 'Microsoft.Web/sites@2023-12-01' = {
  name: functionAppName
  location: location
  kind: 'functionapp,linux'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: plan.id
    siteConfig: {
      linuxFxVersion: 'Python|3.11'
      appSettings: [
        // Identity-based Storage 接続 (allowSharedKeyAccess=false 対応)
        { name: 'AzureWebJobsStorage__accountName', value: storage.name }
        {
          name: 'AzureWebJobsStorage__blobServiceUri'
          value: 'https://${storage.name}.blob.${environment().suffixes.storage}'
        }
        {
          name: 'AzureWebJobsStorage__queueServiceUri'
          value: 'https://${storage.name}.queue.${environment().suffixes.storage}'
        }
        {
          name: 'AzureWebJobsStorage__tableServiceUri'
          value: 'https://${storage.name}.table.${environment().suffixes.storage}'
        }
        {
          name: 'WEBSITE_RUN_FROM_PACKAGE'
          value: 'https://${storage.name}.blob.${environment().suffixes.storage}/function-releases/mcp-deploy.zip'
        }
        { name: 'FUNCTIONS_EXTENSION_VERSION', value: '~4' }
        { name: 'FUNCTIONS_WORKER_RUNTIME', value: 'python' }
        { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsightsConnectionString }
        { name: 'COSMOS_ENDPOINT', value: cosmosEndpoint }
        { name: 'COSMOS_DATABASE', value: 'travel-agent' }
      ]
    }
  }
}

output functionAppName string = functionApp.name
output functionAppHostName string = functionApp.properties.defaultHostName
output mcpEndpoint string = 'https://${functionApp.properties.defaultHostName}/api/mcp'
output principalId string = functionApp.identity.principalId
output storageAccountName string = storage.name

// EasyAuth (Entra ID 認証) — mcpEntraClientId が指定されている場合のみ有効化
resource functionAuthSettings 'Microsoft.Web/sites/config@2023-12-01' = if (mcpEntraClientId != '') {
  parent: functionApp
  name: 'authsettingsV2'
  properties: {
    platform: {
      enabled: true
    }
    globalValidation: {
      requireAuthentication: true
      unauthenticatedClientAction: 'Return401'
    }
    identityProviders: {
      azureActiveDirectory: {
        enabled: true
        registration: {
          clientId: mcpEntraClientId
          openIdIssuer: 'https://sts.windows.net/${entraTenantId}/v2.0'
        }
        validation: {
          allowedAudiences: [
            'api://${mcpEntraClientId}'
            mcpEntraClientId
          ]
        }
      }
    }
  }
}
