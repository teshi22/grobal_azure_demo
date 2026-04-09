// =============================================================================
// Azure Container Registry
// =============================================================================

@description('ACR 名')
param acrName string

@description('デプロイリージョン')
param location string

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: acrName
  location: location
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: true
  }
}

output acrName string = acr.name
output acrLoginServer string = acr.properties.loginServer
