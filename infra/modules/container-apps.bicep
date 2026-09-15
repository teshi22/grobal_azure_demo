// Azure Container Apps — 統合アプリ (Frontend + Backend)
// Managed Identity、イングレス設定含む

@description('リソースのロケーション')
param location string = resourceGroup().location

@description('Container Apps Environment 名')
param envName string

@description('ACR ログインサーバー')
param acrLoginServer string

@description('Log Analytics ワークスペース カスタマー ID')
param logAnalyticsCustomerId string = ''

@secure()
@description('Log Analytics ワークスペース 共有キー')
param logAnalyticsSharedKey string = ''

@description('Cosmos DB エンドポイント')
param cosmosEndpoint string = ''

@description('AI Project エンドポイント')
param aiProjectEndpoint string = ''

@description('Application Insights 接続文字列')
param appInsightsConnectionString string = ''

@description('Foundry Hosted Agent 名')
param hostedAgentName string = 'travel-request-agent'

@description('Web/BFF 用 Entra ID アプリ登録のクライアント ID')
param webEntraClientId string = ''

@description('Entra テナント ID')
param entraTenantId string = subscription().tenantId

// Container Apps Environment (Workload Profile — Consumption)
resource env 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: envName
  location: location
  properties: {
    workloadProfiles: [
      {
        name: 'Consumption'
        workloadProfileType: 'Consumption'
      }
    ]
    appLogsConfiguration: logAnalyticsCustomerId != ''
      ? {
          destination: 'log-analytics'
          logAnalyticsConfiguration: {
            customerId: logAnalyticsCustomerId
            sharedKey: logAnalyticsSharedKey
          }
        }
      : null
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
        targetPort: 80
        transport: 'http'
      }
    }
    template: {
      containers: [
        {
          name: 'app'
          image: 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: [
            { name: 'AZURE_AI_PROJECT_ENDPOINT', value: aiProjectEndpoint }
            { name: 'COSMOS_ENDPOINT', value: cosmosEndpoint }
            { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsightsConnectionString }
            { name: 'HOSTED_AGENT_NAME', value: hostedAgentName }
            { name: 'AZURE_TENANT_ID', value: entraTenantId }
            { name: 'ENTRA_CLIENT_ID', value: webEntraClientId }
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

resource appAuthSettings 'Microsoft.App/containerApps/authConfigs@2024-03-01' = if (webEntraClientId != '') {
  parent: app
  name: 'current'
  properties: {
    platform: {
      enabled: true
    }
    globalValidation: {
      unauthenticatedClientAction: 'RedirectToLoginPage'
      redirectToProvider: 'azureactivedirectory'
      excludedPaths: [
        '/health'
      ]
    }
    identityProviders: {
      azureActiveDirectory: {
        registration: {
          clientId: webEntraClientId
          openIdIssuer: '${environment().authentication.loginEndpoint}${entraTenantId}/v2.0'
        }
        validation: {
          allowedAudiences: [
            webEntraClientId
            'api://${webEntraClientId}'
          ]
        }
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
output appName string = app.name
output appFqdn string = app.properties.configuration.ingress.fqdn
