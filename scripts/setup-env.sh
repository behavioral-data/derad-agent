#!/usr/bin/env bash
# Bootstrap local .env by pulling all secrets from Azure Key Vault.
# Run once after being granted access:
#   az login
#   bash scripts/setup-env.sh
set -euo pipefail

cd "$(dirname "$0")/.."

KV="azkvspzdzrbtv3v4o"
ENV_FILE="agent/llm/.env"

echo "Pulling secrets from Key Vault: $KV"
echo "(Make sure you've run 'az login' first)"
echo ""

pull() {
  az keyvault secret show --vault-name "$KV" --name "$1" --query value -o tsv 2>/dev/null
}

BEARER=$(pull x-bearer-token)
API_KEY=$(pull x-api-key)
API_SECRET=$(pull x-api-secret)

ACCESS_TOKEN=$(pull x-access-token)
ACCESS_TOKEN_SECRET=$(pull x-access-token-secret)
BOT_ID=$(pull bot-user-id)

cat > "$ENV_FILE" <<EOF
# Claude on Azure AI Services (every LLM call in the factcheck pipeline).
# Fill the endpoint + key after creating the deployment; Key Vault doesn't
# currently store them.
AZURE_CLAUDE_ENDPOINT=
AZURE_CLAUDE_API_KEY=
AZURE_CLAUDE_DEPLOYMENT_CHAT=claude-sonnet-4-6

# Search backend.
# Preferred: Claude + web_search_20250305 on the same Foundry resource. Doesn't
# refuse on edgy queries the way gpt-5-mini-search does. Set CLAUDE_SEARCH_DEPLOYMENT
# to the deployment name (claude-haiku-4-5 once deployed; claude-sonnet-4-6 works today).
CLAUDE_SEARCH_DEPLOYMENT=claude-sonnet-4-6
# Fallback (used only when CLAUDE_SEARCH_DEPLOYMENT is unset): Azure OpenAI
# Responses API with gpt-5-mini-search. Refuses on some sensitive queries.
FOUNDRY_PROJECT_ENDPOINT=
FOUNDRY_SEARCH_MODEL=gpt-54-mini-search

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
DERAD_TABLES_ENDPOINT=https://azsaspzdzrbtv3v4o.table.core.windows.net
DERAD_EVENTS_BACKEND=tables
DERAD_PARTICIPANTS_BACKEND=tables

# Local dev settings — keep ingest off so prod stream isn't disrupted
DERAD_INGEST_MODE=off
DERAD_DRY_RUN=false
SERVER_NAME=localhost:5001
EOF

echo "Written: $ENV_FILE"
echo ""
echo "Next steps:"
echo "  pip install -e ."
echo "  flask --app agent.app.app run --port 5001"
