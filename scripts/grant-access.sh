#!/usr/bin/env bash
# Grant a colleague full dev+deploy access to the derad-agent Azure resources.
# Usage: bash scripts/grant-access.sh user@uw.edu
#
# Requires: az login as Owner of the subscription.
set -euo pipefail

USER_EMAIL="${1:?Usage: $0 user@uw.edu}"

RG="rg-derad-agent"
SUBSCRIPTION="faac48db-165b-4928-a952-7d769267fe0b"
KV="azkvlikxqqfjcgk72"
ACR="azacrlikxqqfjcgk72"
STORAGE="azsalikxqqfjcgk72"

echo "Granting access to: $USER_EMAIL"

# Contributor on the resource group (App Service, ACR builds, etc.)
az role assignment create \
  --role "Contributor" \
  --assignee "$USER_EMAIL" \
  --scope "/subscriptions/$SUBSCRIPTION/resourceGroups/$RG" \
  --output none
echo "  ✓ Contributor on $RG"

# Key Vault Secrets User — lets them pull all credentials locally
az role assignment create \
  --role "Key Vault Secrets User" \
  --assignee "$USER_EMAIL" \
  --scope "/subscriptions/$SUBSCRIPTION/resourceGroups/$RG/providers/Microsoft.KeyVault/vaults/$KV" \
  --output none
echo "  ✓ Key Vault Secrets User on $KV"

# Storage Table Data Contributor — local dev reads/writes Azure Tables
az role assignment create \
  --role "Storage Table Data Contributor" \
  --assignee "$USER_EMAIL" \
  --scope "/subscriptions/$SUBSCRIPTION/resourceGroups/$RG/providers/Microsoft.Storage/storageAccounts/$STORAGE" \
  --output none
echo "  ✓ Storage Table Data Contributor on $STORAGE"

# AcrPush — lets them trigger ACR builds and push images
az role assignment create \
  --role "AcrPush" \
  --assignee "$USER_EMAIL" \
  --scope "/subscriptions/$SUBSCRIPTION/resourceGroups/$RG/providers/Microsoft.ContainerRegistry/registries/$ACR" \
  --output none
echo "  ✓ AcrPush on $ACR"

echo ""
echo "Done. Tell $USER_EMAIL to run: bash scripts/setup-env.sh"
