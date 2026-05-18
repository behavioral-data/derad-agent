// derad-agent Phase 3 infra.
// Provisioned by `azd up` against the resource group named by the azd env.
// Identity model: a single user-assigned managed identity (UAMI) is attached
// to the App Service and granted AcrPull, Storage Table Data Contributor, and
// Key Vault Secrets User on the registry, storage account, and vault here.
// No keys, no connection strings, no admin users anywhere.

targetScope = 'resourceGroup'

@minLength(1)
@maxLength(20)
@description('azd env name; embedded in the unique resource token.')
param environmentName string

@description('Location for all resources. Match the existing Azure OpenAI deployment for low-latency access.')
param location string = resourceGroup().location

@description('Tags applied to every resource for cost reporting + traceability.')
param tags object = {
  'azd-env-name': environmentName
  project: 'derad-agent'
}

@description('App Service Plan SKU. B2 (3.5 GB) is the v1 pick.')
param appServicePlanSku string = 'B2'

@description('Public bot handles wired into /info and the tweet href.')
param botHandleAgreeable string = 'aggie_bot'
param botHandleNeutral string = 'nellie_bot'
param botHandleSatirical string = 'eddie_bot'

@description('Restrict to allow-listed authors during supervised launch.')
param restrictToRegistered bool = true

@description('Comma-separated X user ids that are allowed when restricted is true.')
param allowedAuthorIds string = ''

// ── Resource token: deterministic across redeploys ───────────────────────────
var resourceToken = toLower(uniqueString(subscription().id, resourceGroup().id, location, environmentName))

// Resource names follow `az{prefix}{token}`; prefix is <=3 chars, alphanumeric.
var idName  = 'azid${resourceToken}'
var acrName = 'azacr${resourceToken}'
var aspName = 'azasp${resourceToken}'
var appName = 'azapp${resourceToken}'
var saName  = 'azsa${resourceToken}'
var kvName  = 'azkv${resourceToken}'
var logName = 'azlog${resourceToken}'
var aiName  = 'azai${resourceToken}'

// ── User-Assigned Managed Identity ──────────────────────────────────────────
resource uami 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' = {
  name: idName
  location: location
  tags: tags
}

// ── Log Analytics + Application Insights (workspace-based) ──────────────────
resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2025-02-01' = {
  name: logName
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: aiName
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalytics.id
    IngestionMode: 'LogAnalytics'
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
  }
}

// ── Container Registry (Basic, no admin user) ───────────────────────────────
resource acr 'Microsoft.ContainerRegistry/registries@2025-04-01' = {
  name: acrName
  location: location
  tags: tags
  sku: { name: 'Basic' }
  properties: {
    adminUserEnabled: false
    publicNetworkAccess: 'Enabled'
    anonymousPullEnabled: false
  }
}

// ── Storage Account (Tables only; key access off, blob public off) ──────────
resource storage 'Microsoft.Storage/storageAccounts@2024-01-01' = {
  name: saName
  location: location
  tags: tags
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    allowSharedKeyAccess: false
    allowBlobPublicAccess: false
    publicNetworkAccess: 'Enabled'
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    defaultToOAuthAuthentication: true
  }
}

resource tableSvc 'Microsoft.Storage/storageAccounts/tableServices@2024-01-01' = {
  parent: storage
  name: 'default'
}

resource mentionsTable 'Microsoft.Storage/storageAccounts/tableServices/tables@2024-01-01' = {
  parent: tableSvc
  name: 'Mentions'
}

resource rateLimitsTable 'Microsoft.Storage/storageAccounts/tableServices/tables@2024-01-01' = {
  parent: tableSvc
  name: 'RateLimits'
}

// Research event log — append-only structured record per accepted mention.
resource mentionEventsTable 'Microsoft.Storage/storageAccounts/tableServices/tables@2024-01-01' = {
  parent: tableSvc
  name: 'MentionEvents'
}

// Mirror of the drops we didn't process — dedup hits, rate-limit, allow-list,
// self-reply, no-parent, invalid payload. Lets us characterize filtered traffic.
resource mentionDropsTable 'Microsoft.Storage/storageAccounts/tableServices/tables@2024-01-01' = {
  parent: tableSvc
  name: 'MentionDrops'
}

// ── Key Vault (RBAC, purge protection ON) ───────────────────────────────────
resource keyVault 'Microsoft.KeyVault/vaults@2024-11-01' = {
  name: kvName
  location: location
  tags: tags
  properties: {
    tenantId: subscription().tenantId
    sku: { family: 'A', name: 'standard' }
    enableRbacAuthorization: true
    enableSoftDelete: true
    enablePurgeProtection: true
    softDeleteRetentionInDays: 90
    publicNetworkAccess: 'Enabled'
  }
}

// ── App Service Plan (Linux, reserved=true) ─────────────────────────────────
resource appServicePlan 'Microsoft.Web/serverfarms@2024-11-01' = {
  name: aspName
  location: location
  tags: tags
  kind: 'linux'
  sku: { name: appServicePlanSku }
  properties: {
    reserved: true
  }
}

// ── App Service (custom container, UAMI, App Insights conn string) ──────────
resource appService 'Microsoft.Web/sites@2024-11-01' = {
  name: appName
  location: location
  tags: union(tags, { 'azd-service-name': 'derad-agent' })
  kind: 'app,linux,container'
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${uami.id}': {}
    }
  }
  properties: {
    serverFarmId: appServicePlan.id
    httpsOnly: true
    keyVaultReferenceIdentity: uami.id
    siteConfig: {
      // Target the image azd builds and pushes; App Service fails to start
      // until `azd deploy` produces this tag. Better than flapping on a
      // placeholder image whose /healthz responds 404.
      linuxFxVersion: 'DOCKER|${acr.properties.loginServer}/derad-agent:latest'
      acrUseManagedIdentityCreds: true
      acrUserManagedIdentityID: uami.properties.clientId
      alwaysOn: true
      ftpsState: 'Disabled'
      healthCheckPath: '/healthz'
      http20Enabled: true
      cors: {
        allowedOrigins: [
          'https://platform.twitter.com'
        ]
        supportCredentials: false
      }
      appSettings: [
        { name: 'WEBSITES_PORT', value: '8000' }
        { name: 'WEBSITES_ENABLE_APP_SERVICE_STORAGE', value: 'false' }
        { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsights.properties.ConnectionString }
        { name: 'DERAD_AGENT_INDEX_ROOT', value: '/app/indexes' }
        { name: 'SERVER_NAME', value: '${appName}.azurewebsites.net' }
        { name: 'PREFERRED_URL_SCHEME', value: 'https' }
        { name: 'DERAD_RESTRICT_TO_REGISTERED', value: string(restrictToRegistered) }
        { name: 'DERAD_ALLOWED_AUTHOR_IDS', value: allowedAuthorIds }
        { name: 'DERAD_RATE_LIMIT_PER_SEC', value: '3' }
        { name: 'DERAD_STORE_BACKEND', value: 'tables' }
        { name: 'DERAD_EVENTS_BACKEND', value: 'tables' }
        { name: 'DERAD_TABLES_ENDPOINT', value: 'https://${storage.name}.table.core.windows.net' }
        { name: 'AZURE_CLIENT_ID', value: uami.properties.clientId }
        { name: 'BOT_HANDLE_AGREEABLE', value: botHandleAgreeable }
        { name: 'BOT_HANDLE_NEUTRAL', value: botHandleNeutral }
        { name: 'BOT_HANDLE_SATIRICAL', value: botHandleSatirical }
        { name: 'AZURE_OPENAI_API_VERSION', value: '2025-03-01-preview' }
        // Secrets — wired as Key Vault references. Seed the secrets in KV
        // before the first boot (see deployment runbook). The vault and role
        // dependencies below ensure the App Service can resolve these at
        // startup using its UAMI.
        { name: 'AZURE_OPENAI_API_KEY', value: '@Microsoft.KeyVault(SecretUri=${keyVault.properties.vaultUri}secrets/azure-openai-api-key)' }
        { name: 'AZURE_OPENAI_ENDPOINT', value: '@Microsoft.KeyVault(SecretUri=${keyVault.properties.vaultUri}secrets/azure-openai-endpoint)' }
        { name: 'AZURE_OPENAI_DEPLOYMENT_EMBED', value: '@Microsoft.KeyVault(SecretUri=${keyVault.properties.vaultUri}secrets/azure-openai-deployment-embed)' }
        { name: 'AZURE_OPENAI_DEPLOYMENT_CHAT', value: '@Microsoft.KeyVault(SecretUri=${keyVault.properties.vaultUri}secrets/azure-openai-deployment-chat)' }
        { name: 'X_API_KEY', value: '@Microsoft.KeyVault(SecretUri=${keyVault.properties.vaultUri}secrets/x-api-key)' }
        { name: 'X_API_SECRET', value: '@Microsoft.KeyVault(SecretUri=${keyVault.properties.vaultUri}secrets/x-api-secret)' }
        { name: 'X_ACCESS_TOKEN_AGREEABLE', value: '@Microsoft.KeyVault(SecretUri=${keyVault.properties.vaultUri}secrets/x-access-token-agreeable)' }
        { name: 'X_ACCESS_TOKEN_SECRET_AGREEABLE', value: '@Microsoft.KeyVault(SecretUri=${keyVault.properties.vaultUri}secrets/x-access-token-secret-agreeable)' }
        { name: 'X_ACCESS_TOKEN_NEUTRAL', value: '@Microsoft.KeyVault(SecretUri=${keyVault.properties.vaultUri}secrets/x-access-token-neutral)' }
        { name: 'X_ACCESS_TOKEN_SECRET_NEUTRAL', value: '@Microsoft.KeyVault(SecretUri=${keyVault.properties.vaultUri}secrets/x-access-token-secret-neutral)' }
        { name: 'X_ACCESS_TOKEN_SATIRICAL', value: '@Microsoft.KeyVault(SecretUri=${keyVault.properties.vaultUri}secrets/x-access-token-satirical)' }
        { name: 'X_ACCESS_TOKEN_SECRET_SATIRICAL', value: '@Microsoft.KeyVault(SecretUri=${keyVault.properties.vaultUri}secrets/x-access-token-secret-satirical)' }
        { name: 'BOT_USER_ID_AGREEABLE', value: '@Microsoft.KeyVault(SecretUri=${keyVault.properties.vaultUri}secrets/bot-user-id-agreeable)' }
        { name: 'BOT_USER_ID_NEUTRAL', value: '@Microsoft.KeyVault(SecretUri=${keyVault.properties.vaultUri}secrets/bot-user-id-neutral)' }
        { name: 'BOT_USER_ID_SATIRICAL', value: '@Microsoft.KeyVault(SecretUri=${keyVault.properties.vaultUri}secrets/bot-user-id-satirical)' }
      ]
    }
  }
  dependsOn: [
    acrPullAssignment
    kvSecretsUserAssignment
    tableDataContributorAssignment
  ]
}

// Stream App Service runtime logs into Log Analytics.
resource appServiceDiagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  scope: appService
  name: 'ds-${appName}'
  properties: {
    workspaceId: logAnalytics.id
    logs: [
      // AppServiceAppLogs is a no-op for Linux Python containers — only
      // Java SE / Tomcat surface that category. Console logs cover stdout.
      { category: 'AppServiceHTTPLogs', enabled: true }
      { category: 'AppServiceConsoleLogs', enabled: true }
      { category: 'AppServicePlatformLogs', enabled: true }
    ]
    metrics: [
      { category: 'AllMetrics', enabled: true }
    ]
  }
}

// ── Role assignments — UAMI gets everything it needs and nothing more ───────
// AcrPull
var acrPullRoleId = '7f951dda-4ed3-4680-a7ca-43fe172d538d'
resource acrPullAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: acr
  name: guid(acr.id, uami.id, acrPullRoleId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPullRoleId)
    principalId: uami.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// Storage Table Data Contributor
var tableDataContributorRoleId = '0a9a7e1f-b9d0-4cc4-a60d-0319b160aaa3'
resource tableDataContributorAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: storage
  name: guid(storage.id, uami.id, tableDataContributorRoleId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', tableDataContributorRoleId)
    principalId: uami.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// Key Vault Secrets User (read-only on secret values; App Service only needs to read)
var kvSecretsUserRoleId = '4633458b-17de-408a-b874-0445c86b69e6'
resource kvSecretsUserAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: keyVault
  name: guid(keyVault.id, uami.id, kvSecretsUserRoleId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', kvSecretsUserRoleId)
    principalId: uami.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// ── Outputs ─────────────────────────────────────────────────────────────────
output AZURE_RESOURCE_GROUP string = resourceGroup().name
output AZURE_LOCATION string = location
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = acr.properties.loginServer
output AZURE_CONTAINER_REGISTRY_NAME string = acr.name
output AZURE_APP_SERVICE_NAME string = appService.name
output AZURE_APP_SERVICE_HOSTNAME string = appService.properties.defaultHostName
output AZURE_KEY_VAULT_NAME string = keyVault.name
output AZURE_KEY_VAULT_URI string = keyVault.properties.vaultUri
output AZURE_STORAGE_ACCOUNT_NAME string = storage.name
output AZURE_STORAGE_TABLES_ENDPOINT string = 'https://${storage.name}.table.core.windows.net'
output AZURE_LOG_ANALYTICS_WORKSPACE_ID string = logAnalytics.id
output AZURE_APPLICATION_INSIGHTS_CONNECTION_STRING string = appInsights.properties.ConnectionString
output AZURE_USER_ASSIGNED_IDENTITY_NAME string = uami.name
output AZURE_USER_ASSIGNED_IDENTITY_CLIENT_ID string = uami.properties.clientId
output AZURE_USER_ASSIGNED_IDENTITY_PRINCIPAL_ID string = uami.properties.principalId
output AZURE_CONTAINER_IMAGE_NAME string = '${acr.properties.loginServer}/derad-agent:latest'
