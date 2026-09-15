@sys.description('親 AI Services アカウント名')
param accountName string

@sys.description('デプロイリージョン')
param location string

@sys.description('プロジェクト名')
param projectName string

@sys.description('プロジェクトの説明')
param description string

@sys.description('プロジェクトの表示名')
param displayName string

@sys.description('接続する Application Insights の名前')
param appInsightsName string

@sys.description('接続する Application Insights の接続文字列')
param appInsightsConnectionString string

#disable-next-line BCP081
resource account 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' existing = {
  name: accountName
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' existing = {
  name: appInsightsName
}

#disable-next-line BCP081
resource project 'Microsoft.CognitiveServices/accounts/projects@2025-04-01-preview' = {
  parent: account
  name: projectName
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    description: description
    displayName: displayName
  }
}

var appInsightsRoleDefinitionIds = [
  subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '3913510d-42f4-4e42-8a64-420c390055eb') // Monitoring Metrics Publisher
  subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '73c42c96-874c-492b-b04d-ab87d138a893') // Log Analytics Reader
  subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'dbc9c667-e97f-4491-aee6-90b9cf960190') // Privileged Monitoring Data Reader
]

resource appInsightsRoleAssignments 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for roleDefinitionId in appInsightsRoleDefinitionIds: {
  scope: appInsights
  name: guid(appInsights.id, project.id, roleDefinitionId)
  properties: {
    principalId: project.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: roleDefinitionId
  }
}]

resource appInsightsConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-09-01' = {
  parent: project
  name: appInsights.name
  properties: {
    category: 'AppInsights'
    target: appInsights.id
    #disable-next-line BCP036
    authType: 'ProjectManagedIdentity'
    isSharedToAll: false
    metadata: {
      ApiType: 'Azure'
      ResourceId: appInsights.id
      ApplicationInsightsConnectionString: appInsightsConnectionString
    }
  }
  dependsOn: [
    appInsightsRoleAssignments
  ]
}

output projectName string = project.name
output projectId string = project.id
output projectPrincipalId string = project.identity.principalId
