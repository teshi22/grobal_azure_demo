// Azure Container Apps — 統合アプリ (Frontend + Backend)
// Managed Identity、イングレス設定含む

@description('リソースのロケーション')
param location string = resourceGroup().location

@description('Container Apps Environment 名')
param envName string

@description('ACR ログインサーバー')
param acrLoginServer string

@description('Log Analytics ワークスペース ID')
param logAnalyticsWorkspaceId string = ''

@description('Cosmos DB エンドポイント')
param cosmosEndpoint string = ''

@description('AI Project エンドポイント')
param aiProjectEndpoint string = ''

@description('MCP ツールエンドポイント')
param mcpToolEndpoint string = ''

@description('MCP Functions 用 Entra アプリクライアント ID（MI トークン取得用）')
param mcpFunctionAppClientId string = ''

@description('Application Insights 接続文字列')
param appInsightsConnectionString string = ''

// Container Apps Environment
resource env 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: envName
  location: location
  properties: {
    appLogsConfiguration: logAnalyticsWorkspaceId != '' ? {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalyticsWorkspaceId
      }
    } : null
  }
}

// 統合 Container App (Frontend静的配信 + Backend API)
resource app 'Microsoft.App/containerApps@2024-03-01' = {
  name: '${envName}-app'
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    managedEnvironmentId: env.id
    configuration: {
      ingress: {
        external: true
        targetPort: 8000
        transport: 'http'
      }
    }
    template: {
      containers: [
        {
          name: 'app'
          image: 'mcr.microsoft.com/k8se/quickstart:latest'
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: [
            { name: 'AZURE_AI_PROJECT_ENDPOINT', value: aiProjectEndpoint }
            { name: 'COSMOS_ENDPOINT', value: cosmosEndpoint }
            { name: 'MCP_TOOL_ENDPOINT', value: mcpToolEndpoint }
            { name: 'MCP_FUNCTION_APP_CLIENT_ID', value: mcpFunctionAppClientId }
            { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsightsConnectionString }
            { name: 'ENABLE_DOCS', value: 'false' }
          ]
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 5
        rules: [
          {
            name: 'http-rule'
            http: {
              metadata: {
                concurrentRequests: '50'
              }
            }
          }
        ]
      }
    }
  }
}

// ACR Pull ロール: App Managed Identity → ACR
var acrPullRoleId = '7f951dda-4ed3-4680-a7ca-43fe172d538d'

resource appAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(app.id, acrLoginServer, acrPullRoleId)
  scope: resourceGroup()
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPullRoleId)
    principalId: app.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

output appPrincipalId string = app.identity.principalId
output appFqdn string = app.properties.configuration.ingress.fqdn
