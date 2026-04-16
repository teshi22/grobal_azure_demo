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

#disable-next-line BCP081
resource account 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' existing = {
  name: accountName
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

output projectName string = project.name
output projectId string = project.id
output projectPrincipalId string = project.identity.principalId
