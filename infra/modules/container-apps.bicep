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

@description('Cosmos DB データベース名')
param cosmosDatabaseName string = 'travel-agent'

@description('評価ケース Cosmos DB コンテナ名')
param evaluationCaseContainerName string = 'evaluation-cases'

@description('評価実行 Cosmos DB コンテナ名')
param evaluationRunContainerName string = 'evaluation-runs'

@description('評価結果 Cosmos DB コンテナ名')
param evaluationResultContainerName string = 'evaluation-results'

@description('AI Project エンドポイント')
param aiProjectEndpoint string = ''

@description('Application Insights 接続文字列')
param appInsightsConnectionString string = ''

@description('Foundry Hosted Agent 名')
param hostedAgentName string = 'travel-request-agent'

@description('Foundry Hosted Agent バージョン')
param hostedAgentVersion string = ''

@description('単一 Prompt Agent 名')
param singlePromptAgentName string = 'travel-request-single-agent'

@description('単一 Prompt Agent バージョン')
param singlePromptAgentVersion string = ''

@description('単一 Prompt Agent 評価用エージェント名')
param singlePromptEvaluationAgentName string = 'travel-request-single-evaluator'

@description('単一 Prompt Agent 評価用バージョン')
param singlePromptEvaluationAgentVersion string = ''

@description('評価 Judge モデル名')
param evaluationJudgeModel string

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
          image: 'mcr.microsoft.com/azuredocs/containerapps-helloworld@sha256:e9b3e7c34664c7cffd7144864b0e4eec369bfde80068f9095dc63b37058bec48'
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: [
            { name: 'AZURE_AI_PROJECT_ENDPOINT', value: aiProjectEndpoint }
            { name: 'COSMOS_ENDPOINT', value: cosmosEndpoint }
            { name: 'COSMOS_DATABASE', value: cosmosDatabaseName }
            { name: 'COSMOS_EVALUATION_CASE_CONTAINER', value: evaluationCaseContainerName }
            { name: 'COSMOS_EVALUATION_RUN_CONTAINER', value: evaluationRunContainerName }
            { name: 'COSMOS_EVALUATION_RESULT_CONTAINER', value: evaluationResultContainerName }
            { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsightsConnectionString }
            { name: 'HOSTED_AGENT_NAME', value: hostedAgentName }
            { name: 'HOSTED_AGENT_VERSION', value: hostedAgentVersion }
            { name: 'SINGLE_PROMPT_AGENT_NAME', value: singlePromptAgentName }
            { name: 'SINGLE_PROMPT_AGENT_VERSION', value: singlePromptAgentVersion }
            { name: 'SINGLE_PROMPT_EVALUATION_AGENT_NAME', value: singlePromptEvaluationAgentName }
            { name: 'SINGLE_PROMPT_EVALUATION_AGENT_VERSION', value: singlePromptEvaluationAgentVersion }
            { name: 'EVALUATION_JUDGE_MODEL', value: evaluationJudgeModel }
            { name: 'ENTRA_TENANT_ID', value: entraTenantId }
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
