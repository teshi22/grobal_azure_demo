// =============================================================================
// Application Insights モジュール
// =============================================================================

@description('Application Insights 名')
param appInsightsName string

@description('デプロイリージョン')
param location string

@description('Log Analytics ワークスペース名')
param logAnalyticsName string = '${appInsightsName}-logs'

// -----------------------------------------------------------------------------
// Log Analytics Workspace（Application Insights のバックエンド）
// -----------------------------------------------------------------------------
resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: logAnalyticsName
  location: location
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

// -----------------------------------------------------------------------------
// Application Insights
// -----------------------------------------------------------------------------
resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: appInsightsName
  location: location
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalytics.id
  }
}

// -----------------------------------------------------------------------------
// Outputs
// -----------------------------------------------------------------------------
output appInsightsName string = appInsights.name
output connectionString string = appInsights.properties.ConnectionString
output instrumentationKey string = appInsights.properties.InstrumentationKey
output logAnalyticsWorkspaceName string = logAnalytics.name
output logAnalyticsCustomerId string = logAnalytics.properties.customerId
#disable-next-line outputs-should-not-contain-secrets
output logAnalyticsSharedKey string = logAnalytics.listKeys().primarySharedKey
