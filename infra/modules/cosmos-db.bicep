// Cosmos DB アカウント + データベース + コンテナ
// travel-agent アプリケーション用

@description('リソースのロケーション')
param location string = resourceGroup().location

@description('Cosmos DB アカウント名')
param accountName string

@description('データベース名')
param databaseName string = 'travel-agent'

// Cosmos DB アカウント (Serverless)
resource cosmosAccount 'Microsoft.DocumentDB/databaseAccounts@2024-11-15' = {
  name: accountName
  location: location
  kind: 'GlobalDocumentDB'
  tags: {
    SecurityControl: 'Ignore'
  }
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
    publicNetworkAccess: 'Enabled'
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
        paths: ['/workflow_name']
        kind: 'Hash'
        version: 2
      }
      defaultTtl: 2592000 // 30 days
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
          { path: '/event_cursor/?' }
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
        paths: ['/id']
        kind: 'Hash'
        version: 2
      }
    }
  }
}

// コンテナ: approval-grants (バックエンドが発行し、MCP サーバーが条件付き更新)
resource approvalGrantContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-11-15' = {
  parent: database
  name: 'approval-grants'
  properties: {
    resource: {
      id: 'approval-grants'
      partitionKey: {
        paths: ['/id']
        kind: 'Hash'
        version: 2
      }
      defaultTtl: 86400 // 期限切れの承認情報を自動削除
      indexingPolicy: {
        includedPaths: [
          { path: '/conversation_id/?' }
          { path: '/status/?' }
          { path: '/expires_at/?' }
        ]
        excludedPaths: [
          { path: '/*' }
        ]
      }
    }
  }
}

// コンテナ: travel-requests (MCP サーバーが書き込み、バックエンドが読み取り)
resource travelRequestContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-11-15' = {
  parent: database
  name: 'travel-requests'
  properties: {
    resource: {
      id: 'travel-requests'
      partitionKey: {
        paths: ['/request_id']
        kind: 'Hash'
      }
      indexingPolicy: {
        includedPaths: [
          { path: '/user_id/?' }
          { path: '/submitted_at/?' }
          { path: '/status/?' }
        ]
        excludedPaths: [
          { path: '/application_text/*' }
          { path: '/transportation_legs/*' }
          { path: '/*' }
        ]
      }
    }
  }
}

output accountEndpoint string = cosmosAccount.properties.documentEndpoint
output accountName string = cosmosAccount.name
output accountId string = cosmosAccount.id
output databaseName string = database.name
