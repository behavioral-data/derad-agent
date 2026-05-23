#!/usr/bin/env bash
# Seed Key Vault with all required secrets after `azd up` completes.
# Run from the repo root:  bash scripts/seed_keyvault.sh
set -euo pipefail

export PATH="$HOME/.local/bin:$PATH"
cd "$(dirname "$0")/.."

KV_NAME=$(azd env get-values | grep AZURE_KEY_VAULT_NAME | cut -d= -f2 | tr -d '"')
echo "Seeding Key Vault: $KV_NAME"

set_secret() {
  az keyvault secret set --vault-name "$KV_NAME" --name "$1" --value "$2" --output none
  echo "  set $1"
}

# ── Azure OpenAI ─────────────────────────────────────────────────────────────
OPENAI_KEY=$(az cognitiveservices account keys list \
  --name derad-agent-project-resource \
  --resource-group rg-advaitmb-2226 \
  --query key1 -o tsv)

set_secret "azure-openai-api-key"           "$OPENAI_KEY"
set_secret "azure-openai-endpoint"          "https://derad-agent-project-resource.cognitiveservices.azure.com/"
set_secret "azure-openai-deployment-embed"  "text-embedding-3-small"
set_secret "azure-openai-deployment-chat"   "gpt-5-mini"

# ── Azure AI Foundry (Claude chat) ───────────────────────────────────────────
CLAUDE_KEY=$(az cognitiveservices account keys list \
  --name derad-2-resource \
  --resource-group rg-advaitmb-2226 \
  --query key1 -o tsv)

set_secret "azure-claude-api-key"           "$CLAUDE_KEY"
set_secret "azure-claude-endpoint"          "https://derad-2-resource.services.ai.azure.com/"
set_secret "azure-claude-deployment-chat"   "claude-sonnet-4-6"

# ── X / Twitter credentials ──────────────────────────────────────────────────
# Single bot identity (Eddie). Replace placeholders here before running in
# production, or update in KV after.
for name in x-api-key x-api-secret x-bearer-token \
            x-access-token x-access-token-secret \
            bot-user-id; do
  set_secret "$name" "placeholder"
done

APP_NAME=$(azd env get-values | grep AZURE_APP_SERVICE_NAME | cut -d= -f2 | tr -d '"')
RG_NAME=$(azd env get-values | grep AZURE_RESOURCE_GROUP | cut -d= -f2 | tr -d '"')

echo ""
echo "Done. Restarting App Service to pick up secrets..."
az webapp restart --name "$APP_NAME" --resource-group "$RG_NAME"
echo "App Service restarted. Health check: https://${APP_NAME}.azurewebsites.net/healthz"
