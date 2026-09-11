#!/usr/bin/env bash
set -euo pipefail

SUBSCRIPTION=""
LOCATION="${AZURE_LOCATION:-eastus2}"
RESOURCE_GROUP="${AZURE_RESOURCE_GROUP:-rapp-brainstem}"
SUFFIX="${AZURE_RESOURCE_SUFFIX:-$(printf '%s' "$USER" | shasum -a 256 | cut -c1-6)}"
REGISTRY="${AZURE_CONTAINER_REGISTRY:-rappbrainstem${SUFFIX}}"
STORAGE_ACCOUNT="${AZURE_STORAGE_ACCOUNT:-rappbrainstate${SUFFIX}}"
STORAGE_SHARE="${AZURE_STORAGE_SHARE:-brainstemstate}"
STORAGE_MOUNT="${AZURE_STORAGE_MOUNT:-brainstemstate}"
ENVIRONMENT="${AZURE_CONTAINER_APP_ENVIRONMENT:-rapp-brainstem-env}"
APP="${AZURE_CONTAINER_APP_NAME:-rapp-brainstem}"
IMAGE="${REGISTRY}.azurecr.io/rapp-brainstem:latest"
APP_CONFIG="$(mktemp)"
trap 'rm -f "$APP_CONFIG"' EXIT

while [[ $# -gt 0 ]]; do
  case "$1" in
    --subscription)
      SUBSCRIPTION="$2"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

if [[ -z "$SUBSCRIPTION" ]]; then
  echo "--subscription is required" >&2
  exit 2
fi

az account set --subscription "$SUBSCRIPTION"
az extension add --name containerapp --upgrade --only-show-errors
az provider register --namespace Microsoft.App --wait
az provider register --namespace Microsoft.OperationalInsights --wait
az group create --name "$RESOURCE_GROUP" --location "$LOCATION" --output none
if ! az acr show --resource-group "$RESOURCE_GROUP" --name "$REGISTRY" --output none 2>/dev/null; then
  az acr create \
    --resource-group "$RESOURCE_GROUP" \
    --name "$REGISTRY" \
    --sku Basic \
    --admin-enabled true \
    --output none
fi
az acr build --registry "$REGISTRY" --image rapp-brainstem:latest .
if ! az containerapp env show \
  --resource-group "$RESOURCE_GROUP" \
  --name "$ENVIRONMENT" \
  --output none 2>/dev/null; then
  az containerapp env create \
    --resource-group "$RESOURCE_GROUP" \
    --name "$ENVIRONMENT" \
    --location "$LOCATION" \
    --output none
fi

if ! az storage account show \
  --resource-group "$RESOURCE_GROUP" \
  --name "$STORAGE_ACCOUNT" \
  --output none 2>/dev/null; then
  az storage account create \
    --resource-group "$RESOURCE_GROUP" \
    --name "$STORAGE_ACCOUNT" \
    --location "$LOCATION" \
    --kind StorageV2 \
    --sku Standard_LRS \
    --output none
fi
az storage share-rm create \
  --resource-group "$RESOURCE_GROUP" \
  --storage-account "$STORAGE_ACCOUNT" \
  --name "$STORAGE_SHARE" \
  --quota 100 \
  --enabled-protocols SMB \
  --output none
STORAGE_KEY="$(az storage account keys list \
  --resource-group "$RESOURCE_GROUP" \
  --account-name "$STORAGE_ACCOUNT" \
  --query '[0].value' \
  --output tsv)"
az containerapp env storage set \
  --resource-group "$RESOURCE_GROUP" \
  --name "$ENVIRONMENT" \
  --storage-name "$STORAGE_MOUNT" \
  --access-mode ReadWrite \
  --azure-file-account-name "$STORAGE_ACCOUNT" \
  --azure-file-account-key "$STORAGE_KEY" \
  --azure-file-share-name "$STORAGE_SHARE" \
  --output none

if az containerapp show \
  --resource-group "$RESOURCE_GROUP" \
  --name "$APP" \
  --output none 2>/dev/null; then
  az containerapp update \
    --resource-group "$RESOURCE_GROUP" \
    --name "$APP" \
    --image "$IMAGE" \
    --output none
else
  SESSION_SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
  az containerapp create \
    --resource-group "$RESOURCE_GROUP" \
    --name "$APP" \
    --environment "$ENVIRONMENT" \
    --image "$IMAGE" \
    --registry-server "${REGISTRY}.azurecr.io" \
    --registry-username "$(az acr credential show -n "$REGISTRY" --query username -o tsv)" \
    --registry-password "$(az acr credential show -n "$REGISTRY" --query 'passwords[0].value' -o tsv)" \
    --target-port 7071 \
    --ingress external \
    --min-replicas 1 \
    --max-replicas 1 \
    --cpu 1.0 \
    --memory 2Gi \
    --env-vars "RAPP_SESSION_SECRET=secretref:session-secret" \
    --secrets "session-secret=${SESSION_SECRET}" \
    --output none
fi

az containerapp show \
  --resource-group "$RESOURCE_GROUP" \
  --name "$APP" \
  --output json > "$APP_CONFIG"
python3 - "$APP_CONFIG" "$STORAGE_MOUNT" <<'PY'
import json
import sys

path, storage_name = sys.argv[1:]
with open(path, encoding="utf-8") as source:
    app = json.load(source)
configuration = app["properties"]["configuration"]
configuration.pop("secrets", None)
template = app["properties"]["template"]
container = template["containers"][0]
container["volumeMounts"] = [
    {"volumeName": "brainstem-state", "mountPath": "/data"}
]
template["volumes"] = [
    {
        "name": "brainstem-state",
        "storageName": storage_name,
        "storageType": "AzureFile",
    }
]
with open(path, "w", encoding="utf-8") as destination:
    json.dump(app, destination)
PY
az containerapp update \
  --resource-group "$RESOURCE_GROUP" \
  --name "$APP" \
  --yaml "$APP_CONFIG" \
  --output none

FQDN="$(az containerapp show --resource-group "$RESOURCE_GROUP" --name "$APP" --query properties.configuration.ingress.fqdn -o tsv)"
echo "Gateway: https://${FQDN}/mcp"
echo "Health:  https://${FQDN}/health"
