#!/usr/bin/env bash
# Bootstrap local .env by pulling all secrets from Azure Key Vault.
# Run once after being granted access:
#   az login
#   bash scripts/setup-env.sh
set -euo pipefail

cd "$(dirname "$0")/.."

KV="azkvlikxqqfjcgk72"
ENV_FILE="derad_agent/llm/.env"

echo "Pulling secrets from Key Vault: $KV"
echo "(Make sure you've run 'az login' first)"
echo ""

pull() {
  az keyvault secret show --vault-name "$KV" --name "$1" --query value -o tsv 2>/dev/null
}

OPENAI_KEY=$(pull azure-openai-api-key)
OPENAI_ENDPOINT=$(pull azure-openai-endpoint)
OPENAI_EMBED=$(pull azure-openai-deployment-embed)
OPENAI_CHAT=$(pull azure-openai-deployment-chat)

BEARER=$(pull x-bearer-token)
API_KEY=$(pull x-api-key)
API_SECRET=$(pull x-api-secret)

ACCESS_TOKEN=$(pull x-access-token)
ACCESS_TOKEN_SECRET=$(pull x-access-token-secret)
BOT_ID=$(pull bot-user-id)

cat > "$ENV_FILE" <<EOF
# Azure OpenAI Credentials (used for embeddings + fallback chat)
AZURE_OPENAI_API_KEY=$OPENAI_KEY
AZURE_OPENAI_ENDPOINT=$OPENAI_ENDPOINT
AZURE_OPENAI_DEPLOYMENT_EMBED=$OPENAI_EMBED
AZURE_OPENAI_DEPLOYMENT_CHAT=$OPENAI_CHAT
AZURE_OPENAI_API_VERSION=2025-03-01-preview

# Azure AI Services — Grok
AZURE_AI_ENDPOINT=https://derad-agent-project-resource.services.ai.azure.com
AZURE_AI_DEPLOYMENT_CHAT=grok-4.3

# X / Twitter credentials (single bot identity — Eddie)
X_BEARER_TOKEN=$BEARER
X_API_KEY=$API_KEY
X_API_SECRET=$API_SECRET
X_ACCESS_TOKEN=$ACCESS_TOKEN
X_ACCESS_TOKEN_SECRET=$ACCESS_TOKEN_SECRET

# Bot identity
BOT_USER_ID=$BOT_ID
BOT_HANDLE=eddiexbot

# Storage
DERAD_TABLES_ENDPOINT=https://azsalikxqqfjcgk72.table.core.windows.net
DERAD_EVENTS_BACKEND=tables
DERAD_PARTICIPANTS_BACKEND=tables
DERAD_AGENT_INDEX_ROOT=/projects/bdata/advaitmb/derad-agent/indexes

# Local dev settings — keep ingest off so prod stream isn't disrupted
DERAD_INGEST_MODE=off
DERAD_DRY_RUN=false
SERVER_NAME=localhost:5001
EOF

echo "Written: $ENV_FILE"
echo ""
echo "Next steps:"
echo "  pip install -e ."
echo "  flask --app derad_agent.app.app run --port 5001"
