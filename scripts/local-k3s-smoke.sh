#!/usr/bin/env bash
# Run only against a disposable, single-node local-k3s cluster. See docs/local-k3s-smoke.md.
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EXPECTED_CONTEXT="${EXPECTED_CONTEXT:?Set EXPECTED_CONTEXT to the local-k3s context}"
CLUSTER_NAME="${CLUSTER_NAME:?Set CLUSTER_NAME to the local-k3s cluster name}"
STORAGE_CLASS="${STORAGE_CLASS:-csi-hostpath-sc}"
NAMESPACE="${NAMESPACE:-ssu-smoke}"
SERVER_VM="${CLUSTER_NAME}-server-1"
TAG="smoke-$(git -C "$ROOT" rev-parse --short HEAD)-$(date +%s)"
WORK="$(mktemp -d)"
SUCCEEDED=0
VM_NTP_RESTORE=0

for tool in kubectl helm docker multipass openssl python3 local-k3s; do
  command -v "$tool" >/dev/null || { echo "Missing tool: $tool" >&2; exit 1; }
done

k() { kubectl --context "$EXPECTED_CONTEXT" "$@"; }
h() { helm --kube-context "$EXPECTED_CONTEXT" "$@"; }
fail() { echo "FAIL: $*" >&2; exit 1; }
diagnostics() {
  echo "=== Diagnostics: context=$EXPECTED_CONTEXT namespace=$NAMESPACE ===" >&2
  k get nodes -o wide >&2 || true
  k -n "$NAMESPACE" get sts,pod,pvc,deploy,events >&2 || true
  k -n "$NAMESPACE" describe sts smoke-store >&2 || true
  k -n "$NAMESPACE" describe pvc data-smoke-store-0 >&2 || true
  k -n "$NAMESPACE" logs deploy/webhook-rso-webhook-server --tail=100 >&2 || true
  k -n "$NAMESPACE" logs deploy/resize-statefulset-operator --tail=100 >&2 || true
  h -n "$NAMESPACE" list >&2 || true
}
finish() {
  local result=$?
  trap - EXIT
  if (( VM_NTP_RESTORE )); then
    multipass exec "$SERVER_VM" -- sudo timedatectl set-ntp true >&2 || true
  fi
  if (( result != 0 || SUCCEEDED == 0 )); then diagnostics; fi
  rm -rf "$WORK"
  exit "$result"
}
trap finish EXIT

[[ "$EXPECTED_CONTEXT" == "$(kubectl config current-context)" ]] || fail "Current context differs from EXPECTED_CONTEXT"
[[ "$EXPECTED_CONTEXT" == k3s-vm-lab-"$CLUSTER_NAME"-* ]] || fail "Context does not match local-k3s cluster name"
[[ "$CLUSTER_NAME" =~ ^[a-z][a-z0-9-]*[a-z0-9]$ ]] || fail "Invalid CLUSTER_NAME"
[[ "$NAMESPACE" =~ ^[a-z][a-z0-9-]*[a-z0-9]$ ]] || fail "Invalid NAMESPACE"
[[ "$STORAGE_CLASS" =~ ^[a-z0-9][a-z0-9.-]*[a-z0-9]$ ]] || fail "Invalid STORAGE_CLASS"
local-k3s status "$CLUSTER_NAME"
HOST_EPOCH="$(date -u +%s)"
VM_EPOCH="$(multipass exec "$SERVER_VM" -- date -u +%s)"
[[ "$VM_EPOCH" =~ ^[0-9]+$ ]] || fail "Could not read UTC epoch from $SERVER_VM"
CLOCK_SKEW=$((HOST_EPOCH - VM_EPOCH))
if (( CLOCK_SKEW < 0 )); then CLOCK_SKEW=$((-CLOCK_SKEW)); fi
echo "Host/VM clock skew=${CLOCK_SKEW}s"
(( CLOCK_SKEW <= 120 )) || fail "Host and $SERVER_VM clocks differ by ${CLOCK_SKEW}s (>120s); synchronize VM time before deploying fresh TLS (docs/local-k3s-smoke.md)"
[[ "$(k get nodes -o json | python3 -c 'import json,sys; n=json.load(sys.stdin)["items"]; print(len(n) if n and all(any(c["type"]=="Ready" and c["status"]=="True" for c in x["status"]["conditions"]) for x in n) else 0)')" == 1 ]] || fail "Expected exactly one Ready node"
[[ "$(k get sc "$STORAGE_CLASS" -o jsonpath='{.allowVolumeExpansion}')" == true ]] || fail "StorageClass must allow expansion"
[[ "$(k get sc "$STORAGE_CLASS" -o jsonpath='{.provisioner}')" == *.csi.* ]] || fail "StorageClass is not backed by CSI"
if k get namespace "$NAMESPACE" >/dev/null 2>&1; then fail "Namespace $NAMESPACE already exists; clean it up or choose another"; fi
if k get mutatingwebhookconfiguration webhook-rso-webhook-server >/dev/null 2>&1; then fail "Cluster-scoped webhook release already exists"; fi
echo "Context=$EXPECTED_CONTEXT Cluster=$CLUSTER_NAME Node=$SERVER_VM StorageClass=$STORAGE_CLASS Namespace=$NAMESPACE Tag=$TAG"

echo "Building and importing application images"
docker build -f "$ROOT/webhook/Dockerfile" -t "ssu-webhook:$TAG" "$ROOT"
docker build -f "$ROOT/main-resize-operator/Dockerfile" -t "ssu-manual:$TAG" "$ROOT"
for image in "ssu-webhook:$TAG" "ssu-manual:$TAG"; do
  archive="$WORK/${image%%:*}.tar"
  docker save -o "$archive" "$image"
  multipass transfer "$archive" "$SERVER_VM:/tmp/$(basename "$archive")"
  multipass exec "$SERVER_VM" -- sudo k3s ctr --namespace k8s.io images import "/tmp/$(basename "$archive")"
  multipass exec "$SERVER_VM" -- rm "/tmp/$(basename "$archive")"
done

echo "Creating fresh webhook TLS"
openssl req -x509 -newkey rsa:2048 -nodes -days 1 -keyout "$WORK/ca.key" -out "$WORK/ca.crt" -subj '/CN=ssu-smoke-ca' >/dev/null 2>&1
openssl req -newkey rsa:2048 -nodes -keyout "$WORK/server.key" -out "$WORK/server.csr" -subj "/CN=webhook.$NAMESPACE.svc" >/dev/null 2>&1
printf 'subjectAltName=DNS:webhook.%s.svc,DNS:webhook.%s.svc.cluster.local\nextendedKeyUsage=serverAuth\n' "$NAMESPACE" "$NAMESPACE" > "$WORK/server.ext"
openssl x509 -req -in "$WORK/server.csr" -CA "$WORK/ca.crt" -CAkey "$WORK/ca.key" -CAcreateserial -out "$WORK/server.crt" -days 1 -extfile "$WORK/server.ext" >/dev/null 2>&1
CA_BUNDLE="$(base64 < "$WORK/ca.crt" | tr -d '\n')"

echo "Synchronizing dedicated server VM clock after TLS creation"
[[ "$EXPECTED_CONTEXT" == "$(kubectl config current-context)" ]] || fail "Current context changed during image build"
[[ "$EXPECTED_CONTEXT" == k3s-vm-lab-"$CLUSTER_NAME"-* ]] || fail "Context no longer matches local-k3s cluster"
local-k3s status "$CLUSTER_NAME"
multipass info "$SERVER_VM" >/dev/null
echo "TLS certificate $(openssl x509 -in "$WORK/server.crt" -noout -startdate)"
HOST_EPOCH="$(date -u +%s)"
VM_EPOCH="$(multipass exec "$SERVER_VM" -- date -u +%s)"
[[ "$VM_EPOCH" =~ ^[0-9]+$ ]] || fail "Could not read UTC epoch from $SERVER_VM after TLS creation"
echo "Before sync: host_epoch=$HOST_EPOCH vm_epoch=$VM_EPOCH"
VM_NTP_ORIGINAL="$(multipass exec "$SERVER_VM" -- timedatectl show -p NTP --value)"
[[ "$VM_NTP_ORIGINAL" == yes || "$VM_NTP_ORIGINAL" == no ]] || fail "Could not read original NTP setting from $SERVER_VM"
echo "VM original NTP=$VM_NTP_ORIGINAL"
if [[ "$VM_NTP_ORIGINAL" == yes ]]; then
  multipass exec "$SERVER_VM" -- sudo timedatectl set-ntp false
  VM_NTP_RESTORE=1
fi
HOST_EPOCH="$(date -u +%s)"
multipass exec "$SERVER_VM" -- sudo date -u -s "@$HOST_EPOCH"
if (( VM_NTP_RESTORE )); then
  multipass exec "$SERVER_VM" -- sudo timedatectl set-ntp true
  VM_NTP_RESTORE=0
fi
[[ "$(multipass exec "$SERVER_VM" -- timedatectl show -p NTP --value)" == "$VM_NTP_ORIGINAL" ]] || fail "VM NTP setting changed during synchronization"
HOST_EPOCH="$(date -u +%s)"
VM_EPOCH="$(multipass exec "$SERVER_VM" -- date -u +%s)"
[[ "$VM_EPOCH" =~ ^[0-9]+$ ]] || fail "Could not verify UTC epoch from $SERVER_VM after time sync"
CLOCK_SKEW=$((HOST_EPOCH - VM_EPOCH))
if (( CLOCK_SKEW < 0 )); then CLOCK_SKEW=$((-CLOCK_SKEW)); fi
echo "After sync: host_epoch=$HOST_EPOCH vm_epoch=$VM_EPOCH skew=${CLOCK_SKEW}s"
(( CLOCK_SKEW <= 120 )) || fail "VM clock remains >120s from host after synchronization"
CERT_NOT_BEFORE="$(openssl x509 -in "$WORK/server.crt" -noout -startdate)"
CERT_EPOCH="$(python3 -c 'import datetime,sys; print(int(datetime.datetime.strptime(sys.argv[1], "notBefore=%b %d %H:%M:%S %Y GMT").replace(tzinfo=datetime.timezone.utc).timestamp()))' "$CERT_NOT_BEFORE")"
echo "TLS certificate notBefore_epoch=$CERT_EPOCH"
(( VM_EPOCH >= CERT_EPOCH )) || fail "VM clock is still before the fresh TLS certificate validity start; rerun after stabilizing host/VM time"

k create namespace "$NAMESPACE"
k -n "$NAMESPACE" create secret generic admission-tls --from-file=server.crt="$WORK/server.crt" --from-file=server.key="$WORK/server.key"
h upgrade --install webhook "$ROOT/webhook/rso-webhook-server" -n "$NAMESPACE" \
  --set image.repository=ssu-webhook --set image.tag="$TAG" --set image.pullPolicy=Never \
  --set tls.existingSecret=admission-tls --set-string "tls.caBundle=$CA_BUNDLE" --set-json 'imagePullSecrets=[]'
k -n "$NAMESPACE" rollout status deploy/webhook-rso-webhook-server --timeout=180s
h upgrade --install resize-statefulset-operator "$ROOT/main-resize-operator/chart" -n "$NAMESPACE" \
  --set image.repository=ssu-manual --set image.tag="$TAG" --set image.pullPolicy=Never --set-json 'imagePullSecrets=[]'
k -n "$NAMESPACE" rollout status deploy/resize-statefulset-operator --timeout=180s
echo "Both required deployments Ready"

cat <<EOF | k -n "$NAMESPACE" apply -f -
apiVersion: v1
kind: Service
metadata:
  name: smoke-store
spec:
  clusterIP: None
  selector:
    app: smoke-store
  ports:
  - port: 80
    name: http
---
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: smoke-store
spec:
  serviceName: smoke-store
  replicas: 1
  selector:
    matchLabels:
      app: smoke-store
  template:
    metadata:
      labels:
        app: smoke-store
    spec:
      containers:
      - name: store
        image: busybox:1.36
        command: ["sh", "-c", "sleep 360000"]
        volumeMounts:
        - name: data
          mountPath: /data
  volumeClaimTemplates:
  - metadata:
      name: data
    spec:
      storageClassName: $STORAGE_CLASS
      accessModes: ["ReadWriteOnce"]
      resources:
        requests:
          storage: 1Gi
EOF
k -n "$NAMESPACE" rollout status sts/smoke-store --timeout=180s
PVC=data-smoke-store-0
PV="$(k -n "$NAMESPACE" get pvc "$PVC" -o jsonpath='{.spec.volumeName}')"
[[ -n "$PV" ]] || fail "PVC is unbound"
DRIVER="$(k get pv "$PV" -o jsonpath='{.spec.csi.driver}')"
[[ -n "$DRIVER" ]] || fail "Bound PV is not CSI"
echo "PVC=$PVC PV=$PV CSI-driver=$DRIVER"
MARKER="ssu-$TAG"
k -n "$NAMESPACE" exec smoke-store-0 -- sh -c 'echo "$1" > /data/marker' -- "$MARKER"
[[ "$(k -n "$NAMESPACE" exec smoke-store-0 -- cat /data/marker)" == "$MARKER" ]] || fail "Initial marker write failed"

echo "Requesting StatefulSet volumeClaimTemplate expansion from 1Gi to 2Gi"
k -n "$NAMESPACE" patch sts smoke-store --type=json \
  -p '[{"op":"replace","path":"/spec/volumeClaimTemplates/0/spec/resources/requests/storage","value":"2Gi"}]'
ANNOTATION="$(k -n "$NAMESPACE" get sts smoke-store -o jsonpath='{.metadata.annotations.resize-statefulset-operator/resize-0}')"
echo "Admission annotation=$ANNOTATION"
[[ "$ANNOTATION" == 2Gi ]] || fail "Webhook did not produce resize annotation"

for (( attempt=1; attempt<=120; attempt++ )); do
  REQUEST="$(k -n "$NAMESPACE" get pvc "$PVC" -o jsonpath='{.spec.resources.requests.storage}' 2>/dev/null || true)"
  CAPACITY="$(k -n "$NAMESPACE" get pvc "$PVC" -o jsonpath='{.status.capacity.storage}' 2>/dev/null || true)"
  TEMPLATE="$(k -n "$NAMESPACE" get sts smoke-store -o jsonpath='{.spec.volumeClaimTemplates[0].spec.resources.requests.storage}' 2>/dev/null || true)"
  echo "PVC poll $attempt/120: request=$REQUEST capacity=$CAPACITY StatefulSet template=$TEMPLATE"
  if [[ "$REQUEST" == 2Gi && "$CAPACITY" == 2Gi && "$TEMPLATE" == 2Gi ]]; then break; fi
  if (( attempt < 120 )); then sleep 5; fi
done
[[ "$REQUEST" == 2Gi && "$CAPACITY" == 2Gi && "$TEMPLATE" == 2Gi ]] || fail "PVC request, capacity, or recreated StatefulSet template did not reach 2Gi after 120 polls (~10 minutes)"
k -n "$NAMESPACE" rollout status sts/smoke-store --timeout=180s
[[ "$(k -n "$NAMESPACE" exec smoke-store-0 -- cat /data/marker)" == "$MARKER" ]] || fail "Data marker missing after expansion"
echo "=== Webhook log evidence (selected non-sensitive lines) ==="
WEBHOOK_LOG="$(k -n "$NAMESPACE" logs deploy/webhook-rso-webhook-server --tail=100)"
printf '%s\n' "$WEBHOOK_LOG" | grep -E 'Received admission request|new size:|previous size:' | sed -E 's/(Received admission request) .*/\1 [UID redacted]/' || true
echo "=== Manual operator log evidence (selected non-sensitive lines) ==="
OPERATOR_LOG="$(k -n "$NAMESPACE" logs deploy/resize-statefulset-operator --tail=100)"
printf '%s\n' "$OPERATOR_LOG" | grep -E 'Event:|update PVC|Storage size change|Deleting oringinal Statefulset|Delete succussfully' || true
echo "PASS: webhook annotation, CSI PVC request/capacity, StatefulSet readiness, and data marker"
echo "Retained: cluster=$CLUSTER_NAME namespace=$NAMESPACE CSI StorageClass=$STORAGE_CLASS. Cleanup steps: docs/local-k3s-smoke.md"
SUCCEEDED=1
