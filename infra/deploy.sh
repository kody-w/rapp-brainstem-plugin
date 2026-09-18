#!/usr/bin/env bash
set -euo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SUBSCRIPTION=""
SDK_COMMIT="${RAPP_WORK_SOURCE_COMMIT:-}"
SDK_REPOSITORY="https://github.com/kody-w/rapp-work"
SDK_COMMAND="/opt/rapp-work/bin/python -m rapp_work"
SDK_ROOTS="/workspaces"
OWNER_ID="${RAPP_WORK_OWNER_ID:-}"
LOCATION="${AZURE_LOCATION:-eastus2}"
RESOURCE_GROUP="${AZURE_RESOURCE_GROUP:-rapp-brainstem}"
SUFFIX="${AZURE_RESOURCE_SUFFIX:-$(printf '%s' "$USER" | shasum -a 256 | cut -c1-6)}"
REGISTRY="${AZURE_CONTAINER_REGISTRY:-rappbrainstem${SUFFIX}}"
STORAGE_ACCOUNT="${AZURE_STORAGE_ACCOUNT:-rappbrainstate${SUFFIX}}"
STORAGE_SHARE="${AZURE_STORAGE_SHARE:-brainstemstate}"
STORAGE_MOUNT="${AZURE_STORAGE_MOUNT:-brainstemstate}"
WORKSPACE_SHARE="${AZURE_WORKSPACE_SHARE:-rappworkspaces}"
WORKSPACE_MOUNT="${AZURE_WORKSPACE_MOUNT:-rappworkspaces}"
ENVIRONMENT="${AZURE_CONTAINER_APP_ENVIRONMENT:-rapp-brainstem-env}"
APP="${AZURE_CONTAINER_APP_NAME:-rapp-brainstem}"
APP_CONFIG="${SCRIPT_DIR}/.containerapp-${$}.json"
trap 'rm -f "$APP_CONFIG"' EXIT

while [[ $# -gt 0 ]]; do
  case "$1" in
    --subscription)
      SUBSCRIPTION="$2"
      shift 2
      ;;
    --rapp-work-source-commit)
      SDK_COMMIT="$2"
      shift 2
      ;;
    --rapp-work-owner-id)
      OWNER_ID="$2"
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
PIN_RECORD="$(python3 - "$ROOT/RAPP_WORK_SDK_PIN.json" "$SDK_REPOSITORY" <<'PY'
import json
import sys

def reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


with open(sys.argv[1], encoding="utf-8") as source:
    pin = json.load(
        source,
        object_pairs_hook=reject_duplicate_keys,
        parse_constant=lambda value: (_ for _ in ()).throw(
            ValueError(f"Invalid JSON constant: {value}")
        ),
    )
expected_keys = {
    "schema",
    "distribution",
    "sdkVersion",
    "profile",
    "defaultCommand",
    "operations",
    "sourceRepository",
    "sourceCommit",
    "sourceCommitStatus",
}
if not isinstance(pin, dict) or set(pin) != expected_keys:
    raise SystemExit("RAPP Work SDK pin must use its closed manifest shape")
if (
    pin["schema"] != "rapp-work-sdk-consumer-pin/1"
    or pin["distribution"] != "rapp-work"
    or pin["sdkVersion"] != "1.0.0"
    or pin["profile"] != "rapp-work-sdk/1"
    or pin["defaultCommand"] != "python -m rapp_work"
    or pin["operations"]
    != ["status", "verify", "discover", "scaffold", "update", "migrate"]
    or pin["sourceRepository"] != sys.argv[2]
):
    raise SystemExit("RAPP Work SDK pin does not match the deployment contract")
values = (
    pin.get("sourceRepository"),
    pin.get("sourceCommit"),
    pin.get("sourceCommitStatus"),
)
print("|".join(value if isinstance(value, str) else "" for value in values))
PY
)"
IFS="|" read -r PIN_REPOSITORY PIN_COMMIT PIN_STATUS <<< "$PIN_RECORD"
if [[ "$PIN_REPOSITORY" != "$SDK_REPOSITORY" ]]; then
  echo "RAPP Work sourceRepository is not canonical" >&2
  exit 2
fi
if [[ ! "$PIN_COMMIT" =~ ^[0-9a-f]{40}$ || "$PIN_STATUS" != "pinned" ]]; then
  echo "RAPP_WORK_SDK_PIN.json must contain a pinned 40-hex sourceCommit" >&2
  exit 2
fi
if [[ -z "$SDK_COMMIT" ]]; then
  SDK_COMMIT="$PIN_COMMIT"
fi
if [[ ! "$SDK_COMMIT" =~ ^[0-9a-f]{40}$ ]]; then
  echo "A pinned 40-hex RAPP Work source commit is required" >&2
  exit 2
fi
if [[ "$SDK_COMMIT" != "$PIN_COMMIT" ]]; then
  echo "--rapp-work-source-commit must equal RAPP_WORK_SDK_PIN.json" >&2
  exit 2
fi
if [[ "$SDK_COMMAND" != /* || "$SDK_ROOTS" != /* ]]; then
  echo "RAPP Work SDK command and roots must be absolute" >&2
  exit 2
fi
if [[ ! "$OWNER_ID" =~ ^[1-9][0-9]{0,19}$ ]]; then
  echo "--rapp-work-owner-id must be an immutable numeric GitHub user ID" >&2
  exit 2
fi

IMAGE_TAG="sdk-${SDK_COMMIT}"
IMAGE="${REGISTRY}.azurecr.io/rapp-brainstem:${IMAGE_TAG}"

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
az acr build \
  --registry "$REGISTRY" \
  --image "rapp-brainstem:${IMAGE_TAG}" \
  --build-arg "RAPP_WORK_SOURCE_COMMIT=${SDK_COMMIT}" \
  "$ROOT"
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
for share in "$STORAGE_SHARE" "$WORKSPACE_SHARE"; do
  az storage share-rm create \
    --resource-group "$RESOURCE_GROUP" \
    --storage-account "$STORAGE_ACCOUNT" \
    --name "$share" \
    --quota 100 \
    --enabled-protocols SMB \
    --output none
done
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
az containerapp env storage set \
  --resource-group "$RESOURCE_GROUP" \
  --name "$ENVIRONMENT" \
  --storage-name "$WORKSPACE_MOUNT" \
  --access-mode ReadWrite \
  --azure-file-account-name "$STORAGE_ACCOUNT" \
  --azure-file-account-key "$STORAGE_KEY" \
  --azure-file-share-name "$WORKSPACE_SHARE" \
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
    --env-vars \
      "RAPP_SESSION_SECRET=secretref:session-secret" \
      "RAPP_WORK_COMMAND=${SDK_COMMAND}" \
      "RAPP_WORK_ROOTS=${SDK_ROOTS}" \
      "RAPP_WORK_OWNER_IDS=${OWNER_ID}" \
      "RAPP_WORK_SDK_SOURCE_COMMIT=${SDK_COMMIT}" \
    --secrets "session-secret=${SESSION_SECRET}" \
    --output none
fi

az containerapp show \
  --resource-group "$RESOURCE_GROUP" \
  --name "$APP" \
  --output json > "$APP_CONFIG"
python3 - \
  "$APP_CONFIG" \
  "$STORAGE_MOUNT" \
  "$WORKSPACE_MOUNT" \
  "$OWNER_ID" \
  "$SDK_COMMIT" \
  "$SDK_COMMAND" \
  "$SDK_ROOTS" <<'PY'
import json
import sys

(
    path,
    state_storage,
    workspace_storage,
    owner_id,
    sdk_commit,
    sdk_command,
    sdk_roots,
) = sys.argv[1:]
with open(path, encoding="utf-8") as source:
    app = json.load(source)
configuration = app["properties"]["configuration"]
configuration.pop("secrets", None)
template = app["properties"]["template"]
container = template["containers"][0]
container["volumeMounts"] = [
    {"volumeName": "brainstem-state", "mountPath": "/data"},
    {"volumeName": "rapp-workspaces", "mountPath": sdk_roots},
]
template["volumes"] = [
    {
        "name": "brainstem-state",
        "storageName": state_storage,
        "storageType": "AzureFile",
    },
    {
        "name": "rapp-workspaces",
        "storageName": workspace_storage,
        "storageType": "AzureFile",
    },
]
environment = {
    item["name"]: item
    for item in container.get("env", [])
    if isinstance(item, dict) and isinstance(item.get("name"), str)
}
environment.update(
    {
        "RAPP_WORK_COMMAND": {
            "name": "RAPP_WORK_COMMAND",
            "value": sdk_command,
        },
        "RAPP_WORK_ROOTS": {
            "name": "RAPP_WORK_ROOTS",
            "value": sdk_roots,
        },
        "RAPP_WORK_OWNER_IDS": {
            "name": "RAPP_WORK_OWNER_IDS",
            "value": owner_id,
        },
        "RAPP_WORK_SDK_SOURCE_COMMIT": {
            "name": "RAPP_WORK_SDK_SOURCE_COMMIT",
            "value": sdk_commit,
        },
    }
)
container["env"] = [environment[name] for name in sorted(environment)]
container["probes"] = [
    {
        "type": "Liveness",
        "httpGet": {"path": "/health", "port": 7071, "scheme": "HTTP"},
        "initialDelaySeconds": 20,
        "periodSeconds": 30,
        "timeoutSeconds": 5,
        "failureThreshold": 3,
    },
    {
        "type": "Readiness",
        "httpGet": {"path": "/health", "port": 7071, "scheme": "HTTP"},
        "initialDelaySeconds": 5,
        "periodSeconds": 10,
        "timeoutSeconds": 5,
        "failureThreshold": 3,
        "successThreshold": 1,
    },
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
HEALTH_URL="https://${FQDN}/health"
for attempt in {1..24}; do
  if curl --fail --silent --show-error "$HEALTH_URL" >/dev/null; then
    break
  fi
  if [[ "$attempt" == "24" ]]; then
    echo "Deployment did not become ready: ${HEALTH_URL}" >&2
    exit 1
  fi
  sleep 5
done

echo "RAPP Work SDK: ${SDK_COMMIT}"
echo "Gateway: https://${FQDN}/mcp"
echo "Health:  ${HEALTH_URL}"
