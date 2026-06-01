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

@description('Public bot handle (Eddie) wired into /info and the tweet href.')
param botHandle string = 'eddiexbot'

@description('Email for cost and webhook alert notifications. Empty = rules are created but notify nobody.')
param alertEmail string = ''

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
var caeName = 'azcae${resourceToken}'
var cjobName = 'azcjob-eng${resourceToken}'
var fdyName  = 'azfdy${resourceToken}'

// ── Foundry resources (manually provisioned; declared as existing) ───────────
// These were created by `az` CLI; declared here so we can grant UAMI roles and
// reference IDs. Bicep-ifying their creation is a follow-up.
var foundryProjectName = 'derad-factcheck'
var foundryBingConnectionName = 'derad-bing'
var foundrySearchModel = 'gpt-41-mini-search'

resource foundryAccount 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' existing = {
  name: fdyName
}

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

// Mirror of the drops we didn't process — dedup hits, rate-limit, self-reply,
// no-parent, invalid payload. Lets us characterize filtered traffic.
resource mentionDropsTable 'Microsoft.Storage/storageAccounts/tableServices/tables@2024-01-01' = {
  parent: tableSvc
  name: 'MentionDrops'
}

// Registered study participants — metadata only (enrolment, tone assignment).
// The bot now replies to every mention; this table is read for study tracking.
resource participantsTable 'Microsoft.Storage/storageAccounts/tableServices/tables@2024-01-01' = {
  parent: tableSvc
  name: 'Participants'
}

// Engagement snapshots — public metrics polled at the 3-day measurement point.
resource engagementSnapshotsTable 'Microsoft.Storage/storageAccounts/tableServices/tables@2024-01-01' = {
  parent: tableSvc
  name: 'EngagementSnapshots'
}

// Replies to bot posts collected ~3 days after posting for bystander NLP analysis.
resource botReplyRepliesTable 'Microsoft.Storage/storageAccounts/tableServices/tables@2024-01-01' = {
  parent: tableSvc
  name: 'BotReplyReplies'
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
      appSettings: [
        { name: 'WEBSITES_PORT', value: '8000' }
        { name: 'WEBSITES_ENABLE_APP_SERVICE_STORAGE', value: 'false' }
        { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsights.properties.ConnectionString }
        // Live: bot posts replies to mentions. Flip back to 'true' if
        // smoke-testing a sensitive change before letting it land on X.
        { name: 'DERAD_DRY_RUN', value: 'false' }
        { name: 'SERVER_NAME', value: '${appName}.azurewebsites.net' }
        { name: 'PREFERRED_URL_SCHEME', value: 'https' }
        { name: 'DERAD_RATE_LIMIT_PER_SEC', value: '3' }
        { name: 'DERAD_MAX_MENTIONS_PER_DAY', value: '500' }
        { name: 'DERAD_INGEST_MODE', value: 'streaming' }
        { name: 'DERAD_STORE_BACKEND', value: 'tables' }
        { name: 'DERAD_EVENTS_BACKEND', value: 'tables' }
        { name: 'DERAD_PARTICIPANTS_BACKEND', value: 'tables' }
        { name: 'DERAD_TABLES_ENDPOINT', value: 'https://${storage.name}.table.core.windows.net' }
        { name: 'AZURE_CLIENT_ID', value: uami.properties.clientId }
        { name: 'BOT_HANDLE', value: botHandle }
        { name: 'AZURE_OPENAI_API_VERSION', value: '2025-03-01-preview' }
        // Secrets — wired as Key Vault references. Seed the secrets in KV
        // before the first boot (see deployment runbook). The vault and role
        // dependencies below ensure the App Service can resolve these at
        // startup using its UAMI.
        { name: 'AZURE_OPENAI_API_KEY', value: '@Microsoft.KeyVault(SecretUri=${keyVault.properties.vaultUri}secrets/azure-openai-api-key)' }
        { name: 'AZURE_OPENAI_ENDPOINT', value: '@Microsoft.KeyVault(SecretUri=${keyVault.properties.vaultUri}secrets/azure-openai-endpoint)' }
        { name: 'AZURE_OPENAI_DEPLOYMENT_EMBED', value: '@Microsoft.KeyVault(SecretUri=${keyVault.properties.vaultUri}secrets/azure-openai-deployment-embed)' }
        { name: 'AZURE_CLAUDE_ENDPOINT', value: '@Microsoft.KeyVault(SecretUri=${keyVault.properties.vaultUri}secrets/azure-claude-endpoint)' }
        { name: 'AZURE_CLAUDE_API_KEY', value: '@Microsoft.KeyVault(SecretUri=${keyVault.properties.vaultUri}secrets/azure-claude-api-key)' }
        { name: 'AZURE_CLAUDE_DEPLOYMENT_CHAT', value: '@Microsoft.KeyVault(SecretUri=${keyVault.properties.vaultUri}secrets/azure-claude-deployment-chat)' }
        { name: 'X_API_KEY', value: '@Microsoft.KeyVault(SecretUri=${keyVault.properties.vaultUri}secrets/x-api-key)' }
        { name: 'X_API_SECRET', value: '@Microsoft.KeyVault(SecretUri=${keyVault.properties.vaultUri}secrets/x-api-secret)' }
        { name: 'X_BEARER_TOKEN', value: '@Microsoft.KeyVault(SecretUri=${keyVault.properties.vaultUri}secrets/x-bearer-token)' }
        { name: 'X_ACCESS_TOKEN', value: '@Microsoft.KeyVault(SecretUri=${keyVault.properties.vaultUri}secrets/x-access-token)' }
        { name: 'X_ACCESS_TOKEN_SECRET', value: '@Microsoft.KeyVault(SecretUri=${keyVault.properties.vaultUri}secrets/x-access-token-secret)' }
        { name: 'BOT_USER_ID', value: '@Microsoft.KeyVault(SecretUri=${keyVault.properties.vaultUri}secrets/bot-user-id)' }
        // Stage 4 web search.
        // Primary: ClaudeWebSearchBackend (Anthropic web_search_20250305 on the
        // same Foundry resource as chat). Doesn't refuse on edgy queries the way
        // gpt-5-mini-search does. To switch to Haiku once deployed, change this
        // to 'claude-haiku-4-5'.
        { name: 'CLAUDE_SEARCH_DEPLOYMENT', value: 'claude-sonnet-4-6' }
        // Fallback: Azure OpenAI Responses API + gpt-5-mini-search. Only used
        // when CLAUDE_SEARCH_DEPLOYMENT is empty.
        { name: 'FOUNDRY_PROJECT_ENDPOINT', value: 'https://${fdyName}.services.ai.azure.com/api/projects/${foundryProjectName}' }
        { name: 'FOUNDRY_BING_CONNECTION_ID', value: '${foundryAccount.id}/projects/${foundryProjectName}/connections/${foundryBingConnectionName}' }
        { name: 'FOUNDRY_SEARCH_MODEL', value: foundrySearchModel }
        { name: 'FOUNDRY_SEARCH_AGENT_NAME', value: 'derad-bing-search' }
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

// Azure AI Developer on the Foundry account — lets the UAMI create/invoke
// the search agent and read connection metadata.
var azureAiDeveloperRoleId = '64702f94-c441-49e6-a78b-ef80e0188fee'
resource foundryDeveloperAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: foundryAccount
  name: guid(foundryAccount.id, uami.id, azureAiDeveloperRoleId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', azureAiDeveloperRoleId)
    principalId: uami.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// Cognitive Services User on the Foundry account — lets the UAMI invoke the
// gpt-4.1-mini deployment that backs the search agent.
var cogServicesUserRoleId = 'a97b65f3-24c7-4388-baec-2e87135dc908'
resource foundryUserAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: foundryAccount
  name: guid(foundryAccount.id, uami.id, cogServicesUserRoleId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cogServicesUserRoleId)
    principalId: uami.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// ── Container Apps: scheduled engagement polling job ────────────────────────
// Shared environment so future scheduled jobs land in the same workspace.
// Logs forwarded to the existing Log Analytics workspace via shared key — the
// only Container Apps-supported destination that doesn't require a separate
// data-collection-rule resource. The shared key surfaces in deployment history
// (Reader-visible) but is rotatable and only authorizes log ingestion.
resource containerAppsEnv 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: caeName
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
  }
}

// Engagement cron at 00:00 + 12:00 UTC (every 12 h).
// Runs both CLIs in one replica:
//   - derad-poll-engagement  → EngagementSnapshots (~20/reply: every 12h for 10 days)
//   - derad-collect-replies  → BotReplyReplies     (bystander text, once at the 3-day mark)
// Shell wrapper preserves both exit codes so a partial failure surfaces as a
// non-zero replica (visible in the Container Apps run history + log alerts).
// Same UAMI as App Service → AcrPull, Tables, and KV access already in place;
// no extra role assignments needed.
resource engagementCronJob 'Microsoft.App/jobs@2024-03-01' = {
  name: cjobName
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${uami.id}': {}
    }
  }
  properties: {
    environmentId: containerAppsEnv.id
    configuration: {
      triggerType: 'Schedule'
      // 30-minute cap. Polling N replies = N X-API GETs; at ~200ms each the
      // realistic cap is ~9000 replies/run. 30 min is well above expected scale.
      replicaTimeout: 1800
      replicaRetryLimit: 1
      scheduleTriggerConfig: {
        cronExpression: '0 0,12 * * *'
        parallelism: 1
        replicaCompletionCount: 1
      }
      registries: [
        {
          server: acr.properties.loginServer
          identity: uami.id
        }
      ]
      secrets: [
        {
          name: 'x-api-key'
          keyVaultUrl: '${keyVault.properties.vaultUri}secrets/x-api-key'
          identity: uami.id
        }
        {
          name: 'x-api-secret'
          keyVaultUrl: '${keyVault.properties.vaultUri}secrets/x-api-secret'
          identity: uami.id
        }
        {
          name: 'x-access-token'
          keyVaultUrl: '${keyVault.properties.vaultUri}secrets/x-access-token'
          identity: uami.id
        }
        {
          name: 'x-access-token-secret'
          keyVaultUrl: '${keyVault.properties.vaultUri}secrets/x-access-token-secret'
          identity: uami.id
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'engagement'
          image: '${acr.properties.loginServer}/derad-agent:latest'
          command: ['/bin/sh', '-c']
          // Run both CLIs; exit non-zero iff either failed.
          args: ['derad-poll-engagement; eng=$?; derad-collect-replies; col=$?; exit $((eng + col))']
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: [
            { name: 'DERAD_EVENTS_BACKEND', value: 'tables' }
            { name: 'DERAD_TABLES_ENDPOINT', value: 'https://${storage.name}.table.core.windows.net' }
            { name: 'AZURE_CLIENT_ID', value: uami.properties.clientId }
            { name: 'X_API_KEY', secretRef: 'x-api-key' }
            { name: 'X_API_SECRET', secretRef: 'x-api-secret' }
            { name: 'X_ACCESS_TOKEN', secretRef: 'x-access-token' }
            { name: 'X_ACCESS_TOKEN_SECRET', secretRef: 'x-access-token-secret' }
          ]
        }
      ]
    }
  }
  dependsOn: [
    acrPullAssignment
    kvSecretsUserAssignment
    tableDataContributorAssignment
  ]
}

// ── Phase 6: Observability — action group, budget, and alert rules ──────────

// One action group shared by all alert rules. Always created; only sends email
// when alertEmail is non-empty so the resource can be provisioned in dry-run
// environments without forcing a contact address.
resource actionGroup 'Microsoft.Insights/actionGroups@2023-01-01' = {
  name: 'ag${resourceToken}'
  location: 'global'
  tags: tags
  properties: {
    enabled: true
    groupShortName: 'derad'
    emailReceivers: empty(alertEmail) ? [] : [
      {
        name: 'ops'
        emailAddress: alertEmail
        useCommonAlertSchema: true
      }
    ]
  }
}

// Monthly cost budget. Alerts at 50 % ($200 warn) and 100 % ($400 critical).
// startDate must be the first day of a billing month; update when renewing.
// Skipped (no notifications) when alertEmail is blank — budget is still tracked
// in the portal for manual review.
resource budget 'Microsoft.Consumption/budgets@2023-11-01' = {
  name: 'derad-monthly'
  properties: {
    timePeriod: { startDate: '2026-05-01' }
    timeGrain: 'Monthly'
    amount: 400
    category: 'Cost'
    notifications: empty(alertEmail) ? {} : {
      warn: {
        enabled: true
        operator: 'GreaterThanOrEqualTo'
        threshold: 50 // 50 % of $400 = $200
        thresholdType: 'Actual'
        contactEmails: [alertEmail]
        contactRoles: []
        contactGroups: []
        locale: 'en-us'
      }
      critical: {
        enabled: true
        operator: 'GreaterThanOrEqualTo'
        threshold: 100 // 100 % of $400
        thresholdType: 'Actual'
        contactEmails: [alertEmail]
        contactRoles: []
        contactGroups: []
        locale: 'en-us'
      }
    }
  }
}

// Metric alert: HTTP 5xx count > 5 in any 10-minute window.
// Uses the App Service platform metric `Http5xx` (a raw count, not a rate).
// Threshold of 5 catches a sustained regression; one or two isolated 5xx from
// transient X timeouts won't page. Adjust threshold to taste.
resource alert5xx 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: 'alert-5xx-${appName}'
  location: 'global'
  tags: tags
  properties: {
    description: 'HTTP 5xx count > 5 in a 10-minute window — likely a deployment regression or unhandled exception.'
    severity: 2
    enabled: true
    scopes: [appService.id]
    evaluationFrequency: 'PT5M'
    windowSize: 'PT10M'
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          name: 'Http5xxCount'
          criterionType: 'StaticThresholdCriterion'
          metricName: 'Http5xx'
          operator: 'GreaterThan'
          threshold: 5
          timeAggregation: 'Total'
        }
      ]
    }
    actions: [{ actionGroupId: actionGroup.id }]
  }
}

// Log alert: no accepted mentions logged in 24 hours.
// Queries AppTraces (console logs) in Log Analytics for the "Accepted mention" line
// that _dispatch_tweet emits on every successfully accepted tweet. Fires when the
// filtered stream goes silent — usually means the bearer token expired, the stream
// rules were wiped, or the App Service restarted and failed to reconnect.
resource alertZeroMentions 'Microsoft.Insights/scheduledQueryRules@2022-06-15' = {
  name: 'alert-zero-mentions-${appName}'
  location: location
  tags: tags
  properties: {
    description: 'No accepted mentions in 24 h — filtered stream may be disconnected or bearer token expired.'
    severity: 3
    enabled: true
    evaluationFrequency: 'PT1H'
    windowSize: 'P1D'
    scopes: [logAnalytics.id]
    criteria: {
      allOf: [
        {
          query: 'AppTraces | where TimeGenerated > ago(24h) | where Message contains "Accepted mention"'
          timeAggregation: 'Count'
          threshold: 1
          operator: 'LessThan'
          failingPeriods: {
            minFailingPeriodsToAlert: 1
            numberOfEvaluationPeriods: 1
          }
        }
      ]
    }
    actions: { actionGroups: [actionGroup.id] }
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
output AZURE_CONTAINER_APPS_ENV_NAME string = containerAppsEnv.name
output AZURE_ENGAGEMENT_CRON_JOB_NAME string = engagementCronJob.name
