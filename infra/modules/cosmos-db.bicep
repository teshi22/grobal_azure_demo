// Cosmos DB アカウント + データベース + コンテナ
// travel-agent アプリケーション用

@description('リソースのロケーション')
param location string = resourceGroup().location

@description('Cosmos DB アカウント名')
param accountName string

@description('データベース名')
param databaseName string = 'travel-agent'

@description('バックエンド Container App のプリンシパル ID (RBAC用)')
param backendPrincipalId string = ''

// Cosmos DB アカウント (Serverless)
resource cosmosAccount 'Microsoft.DocumentDB/databaseAccounts@2024-11-15' = {
  name: accountName
  location: location
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    capabilities: [
      { name: 'EnableServerless' }
    ]
    consistencyPolicy: {
      defaultConsistencyLevel: 'Session'
    }
    locations: [
      {
        locationName: location
        failoverPriority: 0
      }
    ]
  }
}

// データベース
resource database 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-11-15' = {
  parent: cosmosAccount
  name: databaseName
  properties: {
    resource: {
      id: databaseName
    }
  }
}

// コンテナ: workflow-checkpoints
resource checkpointContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-11-15' = {
  parent: database
  name: 'workflow-checkpoints'
  properties: {
    resource: {
      id: 'workflow-checkpoints'
      partitionKey: {
        paths: ['/conversation_id']
        kind: 'Hash'
      }
      defaultTtl: 86400 // 24h TTL
    }
  }
}

// コンテナ: conversation-events
resource eventContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-11-15' = {
  parent: database
  name: 'conversation-events'
  properties: {
    resource: {
      id: 'conversation-events'
      partitionKey: {
        paths: ['/conversation_id']
        kind: 'Hash'
      }
      indexingPolicy: {
        includedPaths: [
          { path: '/conversation_id/?' }
          { path: '/event_index/?' }
          { path: '/idempotency_key/?' }
        ]
        excludedPaths: [
          { path: '/data/*' }
          { path: '/*' }
        ]
      }
    }
  }
}

// コンテナ: conversations
resource conversationContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-11-15' = {
  parent: database
  name: 'conversations'
  properties: {
    resource: {
      id: 'conversations'
      partitionKey: {
        paths: ['/user_id']
        kind: 'Hash'
      }
    }
  }
}

// RBAC: バックエンドに Cosmos DB データ投稿者ロールを付与
var cosmosDataContributorRoleId = '00000000-0000-0000-0000-000000000002'

resource backendCosmosRbac 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-11-15' = if (backendPrincipalId != '') {
  parent: cosmosAccount
  name: guid(cosmosAccount.id, backendPrincipalId, cosmosDataContributorRoleId)
  properties: {
    roleDefinitionId: '${cosmosAccount.id}/sqlRoleDefinitions/${cosmosDataContributorRoleId}'
    principalId: backendPrincipalId
    scope: cosmosAccount.id
  }
}

output accountEndpoint string = cosmosAccount.properties.documentEndpoint
output accountName string = cosmosAccount.name
