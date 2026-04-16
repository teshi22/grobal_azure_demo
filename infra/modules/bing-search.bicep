@description('親 AI Services アカウント名')
param accountName string

@description('Bing Search リソース名')
param bingSearchName string = 'bingsearch-${accountName}'

#disable-next-line BCP081
resource account 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' existing = {
  name: accountName
}

#disable-next-line BCP081
resource bingSearch 'Microsoft.Bing/accounts@2020-06-10' = {
  name: bingSearchName
  location: 'global'
  sku: {
    name: 'G1'
  }
  kind: 'Bing.Grounding'
}

#disable-next-line BCP081
resource bingConnection 'Microsoft.CognitiveServices/accounts/connections@2025-04-01-preview' = {
  name: '${accountName}-bing-grounding'
  parent: account
  properties: {
    category: 'ApiKey'
    target: 'https://api.bing.microsoft.com/'
    authType: 'ApiKey'
    credentials: {
      key: bingSearch.listKeys().key1
    }
    isSharedToAll: true
    metadata: {
      ApiType: 'Azure'
      Location: bingSearch.location
      ResourceId: bingSearch.id
    }
  }
}

output connectionName string = bingConnection.name
output bingSearchName string = bingSearch.name
