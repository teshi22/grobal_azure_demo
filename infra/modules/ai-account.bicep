@description('AI Services アカウント名')
param accountName string

@description('デプロイリージョン')
param location string

@description('モデル名')
param modelName string

@description('モデルフォーマット')
param modelFormat string

@description('モデルバージョン')
param modelVersion string

@description('モデル SKU 名')
param modelSkuName string

@description('モデルキャパシティ (TPM)')
param modelCapacity int

#disable-next-line BCP081
resource account 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' = {
  name: accountName
  location: location
  sku: {
    name: 'S0'
  }
  kind: 'AIServices'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    allowProjectManagement: true
    customSubDomainName: accountName
    networkAcls: {
      defaultAction: 'Allow'
      virtualNetworkRules: []
      ipRules: []
    }
    publicNetworkAccess: 'Enabled'
    disableLocalAuth: true
  }
}

resource modelDeployment 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: account
  name: modelName
  sku: {
    capacity: modelCapacity
    name: modelSkuName
  }
  properties: {
    model: {
      name: modelName
      format: modelFormat
      version: modelVersion
    }
  }
}

output accountName string = account.name
output accountId string = account.id
output endpoint string = 'https://${account.name}.services.ai.azure.com/'
