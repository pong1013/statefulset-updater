# Local k3s expansion smoke test

This runbook evaluates the required webhook and manual operator on one disposable local-k3s server. It leaves the cluster and test resources available for inspection. It does not test the automatic operator or certify a production storage driver.

## Prerequisites and cluster

Use macOS with `local-k3s`, Multipass, Docker, kubectl, Helm 3, OpenSSL, Python 3, and Git. Docker must be running. Allow enough VM disk space for k3s, CSI images, and two application images. Run commands from this repository's root.

```sh
local-k3s doctor
local-k3s create statefulset-updater-mvp
```

At the interactive prompts, choose **one total node** (the server), no fake GPU, and no monitoring. Choose a VM with at least 2 CPUs, 4 GiB RAM, and 30 GiB disk. `local-k3s create` merges a context named `k3s-vm-lab-statefulset-updater-mvp-<cluster-id>` into kubeconfig. If the cluster already exists, use `local-k3s start statefulset-updater-mvp` when stopped and `local-k3s status statefulset-updater-mvp wide` to inspect it. Do not run `create` a second time for an existing cluster.

```sh
export CLUSTER_NAME=statefulset-updater-mvp
export EXPECTED_CONTEXT="$(kubectl config current-context)"
printf 'Context: %s\n' "$EXPECTED_CONTEXT"
kubectl --context "$EXPECTED_CONTEXT" get nodes -o wide
local-k3s status "$CLUSTER_NAME" wide
```

Before any mutation, confirm that `EXPECTED_CONTEXT` names the intended `k3s-vm-lab-statefulset-updater-mvp-<cluster-id>` context and has exactly one Ready node. The smoke script enforces this again. The CSI install commands below use kubectl's **current** context, so verify it immediately before running them.

## Install the test CSI driver

The default k3s `local-path` StorageClass does not establish completed CSI expansion. Use the official [CSI Hostpath test driver](https://github.com/kubernetes-csi/csi-driver-host-path/tree/v1.18.0), pinned to `v1.18.0`. It is a demonstration driver for a single node. Its [deployment guide](https://github.com/kubernetes-csi/csi-driver-host-path/blob/v1.18.0/docs/deploy-1.17-and-later.md) requires snapshot CRDs and a snapshot controller. The following commands keep the upstream checkout outside this repository and use pinned upstream tags. Review the current context again just before the first `kubectl apply`.

```sh
(
set -eu
: "${EXPECTED_CONTEXT:?Set EXPECTED_CONTEXT to the local-k3s context}"
: "${CLUSTER_NAME:?Set CLUSTER_NAME to the local-k3s cluster name}"
test "$(kubectl config current-context)" = "$EXPECTED_CONTEXT"
if test ! -d /tmp/ssu-csi-driver-host-path-v1.18.0; then
  git clone --depth 1 --branch v1.18.0 https://github.com/kubernetes-csi/csi-driver-host-path.git /tmp/ssu-csi-driver-host-path-v1.18.0
fi
test "$(git -C /tmp/ssu-csi-driver-host-path-v1.18.0 describe --tags --exact-match)" = v1.18.0
export SNAPSHOTTER_VERSION=v6.3.3
for resource in volumesnapshotclasses volumesnapshotcontents volumesnapshots; do
  kubectl --context "$EXPECTED_CONTEXT" apply -f "https://raw.githubusercontent.com/kubernetes-csi/external-snapshotter/${SNAPSHOTTER_VERSION}/client/config/crd/snapshot.storage.k8s.io_${resource}.yaml"
done
kubectl --context "$EXPECTED_CONTEXT" apply -f "https://raw.githubusercontent.com/kubernetes-csi/external-snapshotter/${SNAPSHOTTER_VERSION}/deploy/kubernetes/snapshot-controller/rbac-snapshot-controller.yaml"
kubectl --context "$EXPECTED_CONTEXT" apply -f "https://raw.githubusercontent.com/kubernetes-csi/external-snapshotter/${SNAPSHOTTER_VERSION}/deploy/kubernetes/snapshot-controller/setup-snapshot-controller.yaml"
kubectl --context "$EXPECTED_CONTEXT" -n kube-system rollout status deployment/snapshot-controller --timeout=180s
SERVER_VM="${CLUSTER_NAME}-server-1"
KUBELET_DATA_DIR="$(multipass exec "$SERVER_VM" -- sh -c 'for dir in /var/lib/kubelet /var/lib/rancher/k3s/agent/kubelet; do if test -d "$dir/plugins" && test -d "$dir/plugins_registry"; then printf "%s" "$dir"; exit 0; fi; done; exit 1')"
printf 'Using VM %s kubelet directory %s\n' "$SERVER_VM" "$KUBELET_DATA_DIR"
test "$(kubectl config current-context)" = "$EXPECTED_CONTEXT"
KUBELET_DATA_DIR="$KUBELET_DATA_DIR" /tmp/ssu-csi-driver-host-path-v1.18.0/deploy/kubernetes-latest/deploy.sh
kubectl --context "$EXPECTED_CONTEXT" apply -f /tmp/ssu-csi-driver-host-path-v1.18.0/examples/csi-storageclass.yaml
kubectl --context "$EXPECTED_CONTEXT" get sc csi-hostpath-sc -o custom-columns=NAME:.metadata.name,PROVISIONER:.provisioner,EXPAND:.allowVolumeExpansion
kubectl --context "$EXPECTED_CONTEXT" rollout status sts/csi-hostpathplugin --timeout=300s
)
```

The expected StorageClass is `csi-hostpath-sc`, with `provisioner: hostpath.csi.k8s.io` and `allowVolumeExpansion: true`. The smoke script verifies those properties and then verifies that the bound PV has a CSI driver. The runbook checks the server VM for the kubelet `plugins` and `plugins_registry` directories and passes their parent as `KUBELET_DATA_DIR` to the upstream deployment script. On the observed local-k3s v1.36.4 VM, that parent is `/var/lib/kubelet`; using `/var/lib/rancher/k3s/agent/kubelet` caused the CSI pod to fail its hostPath mounts. If the upstream driver cannot install, retain its logs and report the environment failure; do not claim an application pass.

If recovering from that failed install, the StatefulSet template may contain the corrected path while its existing `csi-hostpathplugin-0` Pod still has the old path and remains `ContainerCreating`. Inspect both objects first. Only when the Pod is stuck and the guarded paths differ, recreate **that CSI test Pod only** and wait for rollout:

```sh
(
set -eu
: "${EXPECTED_CONTEXT:?Set EXPECTED_CONTEXT to the local-k3s context}"
test "$(kubectl config current-context)" = "$EXPECTED_CONTEXT"
kubectl --context "$EXPECTED_CONTEXT" -n default get sts csi-hostpathplugin -o jsonpath='{range .spec.template.spec.volumes[*]}{.name}={.hostPath.path}{"\n"}{end}'
kubectl --context "$EXPECTED_CONTEXT" -n default get pod csi-hostpathplugin-0 -o jsonpath='{range .spec.volumes[*]}{.name}={.hostPath.path}{"\n"}{end}'
kubectl --context "$EXPECTED_CONTEXT" -n default get pod csi-hostpathplugin-0
TEMPLATE_PATH="$(kubectl --context "$EXPECTED_CONTEXT" -n default get sts csi-hostpathplugin -o jsonpath='{.spec.template.spec.volumes[?(@.name=="registration-dir")].hostPath.path}')"
POD_PATH="$(kubectl --context "$EXPECTED_CONTEXT" -n default get pod csi-hostpathplugin-0 -o jsonpath='{.spec.volumes[?(@.name=="registration-dir")].hostPath.path}')"
test "$TEMPLATE_PATH" = /var/lib/kubelet/plugins_registry
test "$POD_PATH" != "$TEMPLATE_PATH"
test "$(kubectl --context "$EXPECTED_CONTEXT" -n default get pod csi-hostpathplugin-0 -o jsonpath='{.status.phase}')" = Pending
test "$(kubectl config current-context)" = "$EXPECTED_CONTEXT"
kubectl --context "$EXPECTED_CONTEXT" -n default delete pod csi-hostpathplugin-0
kubectl --context "$EXPECTED_CONTEXT" -n default rollout status sts/csi-hostpathplugin --timeout=300s
)
```

You may provide another verified expansion-capable CSI class with `STORAGE_CLASS=<name>`. A class with `allowVolumeExpansion: true` alone is insufficient: the bound PV must be CSI and its reported capacity must increase.

## Run the application smoke test

The script builds images from this checkout, imports them into the single k3s VM, creates a one-day CA and server certificate in a temporary directory, and installs only `webhook` and `resize-statefulset-operator`. The cert private keys are removed when the script exits. It uses the reserved namespace `ssu-smoke` by default. The webhook chart also creates cluster-scoped resources, so the script refuses a pre-existing namespace or `webhook-rso-webhook-server` configuration. Its fixed Service name requires the Helm release name `webhook`.

The script compares host and server VM UTC clocks before building images and stops if their skew exceeds 120 seconds. A long Docker build can cause this lab VM's clock to diverge again. Immediately after generating fresh TLS, the script rechecks the exact context and VM, sets that VM's clock from the host, preserves the VM's original NTP setting, verifies the resulting skew and that VM time is past the certificate's `notBefore`, and records the certificate validity time plus before/after epochs. These steps run before namespace creation or Helm installation, because a fresh webhook certificate can otherwise be `not yet valid` to the API server. If the first preflight fails, inspect the exact `local-k3s` cluster and synchronize **only its server VM**. On the observed run, the VM was more than six hours behind while NTP was active but unsynchronized; later NTP was disabled. The following guarded block reads the original NTP setting, pauses it only when enabled, sets VM time from the host UTC epoch, and restores the original setting even if setting the time fails:

```sh
(
set -eu
: "${CLUSTER_NAME:?Set CLUSTER_NAME to the local-k3s cluster name}"
: "${EXPECTED_CONTEXT:?Set EXPECTED_CONTEXT to the local-k3s context}"
test "$(kubectl config current-context)" = "$EXPECTED_CONTEXT"
case "$EXPECTED_CONTEXT" in "k3s-vm-lab-${CLUSTER_NAME}-"*) ;; *) echo 'Unexpected context' >&2; exit 1;; esac
local-k3s status "$CLUSTER_NAME"
SERVER_VM="${CLUSTER_NAME}-server-1"
multipass info "$SERVER_VM"
printf 'Host UTC: '; date -u
printf 'VM UTC: '; multipass exec "$SERVER_VM" -- date -u
HOST_EPOCH="$(date -u +%s)"
NTP_ORIGINAL="$(multipass exec "$SERVER_VM" -- timedatectl show -p NTP --value)"
case "$NTP_ORIGINAL" in yes|no) ;; *) echo 'Cannot read VM NTP setting' >&2; exit 1;; esac
if test "$NTP_ORIGINAL" = yes; then
  multipass exec "$SERVER_VM" -- sudo timedatectl set-ntp false
  trap 'multipass exec "$SERVER_VM" -- sudo timedatectl set-ntp true' EXIT
fi
multipass exec "$SERVER_VM" -- sudo date -u -s "@$HOST_EPOCH"
if test "$NTP_ORIGINAL" = yes; then
  multipass exec "$SERVER_VM" -- sudo timedatectl set-ntp true
  trap - EXIT
fi
test "$(multipass exec "$SERVER_VM" -- timedatectl show -p NTP --value)" = "$NTP_ORIGINAL"
printf 'VM UTC after sync: '; multipass exec "$SERVER_VM" -- date -u
)
```

After a clock-related admission failure, use [Rerun and cleanup](#rerun-and-cleanup) to remove the disposable namespace and Helm releases, then rerun the smoke script so it generates fresh TLS. Keep the previous evidence log to show the original failure.

```sh
export CLUSTER_NAME=statefulset-updater-mvp
export EXPECTED_CONTEXT="$(kubectl config current-context)"
mkdir -p /tmp/ssu-evidence
export EVIDENCE_FILE="/tmp/ssu-evidence/smoke-$(date +%Y%m%d-%H%M%S).log"
bash -o pipefail -c 'bash scripts/local-k3s-smoke.sh 2>&1 | tee "$EVIDENCE_FILE"'
```

The explicit Bash `pipefail` ensures that a failing smoke script remains a failed command even when `tee` succeeds. Keep logs outside the repository. The script prints the context, image tag, namespace, Ready results, CSI driver, admission annotation, PVC request and capacity, StatefulSet template, final marker result, and selected webhook and operator log lines. Admission UIDs are redacted from successful-path log evidence; private keys and Secret contents are never printed. The final `PASS` line appears only after all checks succeed. Any failure exits nonzero and prints relevant nodes, workload objects, events, descriptions, component logs, and Helm release status. Collect the exact failed step before writing a bug report.

The workload is a `busybox:1.36` StatefulSet. Its single PVC starts at `1Gi`, and the script writes a marker before patching the volumeClaimTemplate to `2Gi` through the Kubernetes API. It requires the webhook's `resize-statefulset-operator/resize-0=2Gi` annotation, the operator's PVC request and recreated template at `2Gi`, the CSI reported capacity at `2Gi`, a Ready StatefulSet, and the same marker after expansion.

PVC request and reported capacity may advance at different times. The script checks them up to 120 times with five-second pauses (about ten minutes) using a fixed attempt count, so host or VM clock corrections do not prematurely end the wait. It still exits nonzero with diagnostics if the capacity has not reached `2Gi` by the last check.

## Rerun and cleanup

The cluster is intentionally retained. The `ssu-smoke` test namespace contains the disposable application, PVC, TLS Secret, and both Helm releases. To rerun, inspect any failure first, then remove the test releases and namespace. Confirm the exact context and namespace before these destructive commands:

```sh
(
set -eu
: "${EXPECTED_CONTEXT:?Set EXPECTED_CONTEXT to the local-k3s context}"
test "$(kubectl config current-context)" = "$EXPECTED_CONTEXT"
helm --kube-context "$EXPECTED_CONTEXT" -n ssu-smoke uninstall resize-statefulset-operator webhook
kubectl --context "$EXPECTED_CONTEXT" delete namespace ssu-smoke --wait=true
)
```

The CSI driver, its StorageClass, snapshot resources, VM, and kubeconfig remain. To remove the test CSI driver later, use the pinned upstream `deploy/kubernetes-latest/destroy.sh` on this isolated cluster, then remove its StorageClass and snapshot prerequisites only if no other workload uses them. `local-k3s delete statefulset-updater-mvp` destroys the entire local cluster and is a separate action. Do not run that command when retaining evidence.

## Existing CI checks and defect reports

Run the repository's complete static checks after editing the smoke workflow:

```sh
python3 -m py_compile webhook/webhook_server.py main-resize-operator/resize_sts_operator.py auto-resize-operator/auto.py
python3 -m unittest discover -s tests -v
helm lint main-resize-operator/chart --set image.repository=example/operator
helm lint auto-resize-operator/auto-resize --set image.repository=example/operator
helm lint webhook/rso-webhook-server --set image.repository=example/webhook --set tls.existingSecret=rotated-webhook-tls --set tls.caBundle=Y2E=
```

For each reproducible repository defect, open a separate GitHub issue after reviewing the exact title and body. Include cluster context and versions, component image tag, steps, expected and actual Kubernetes API state, relevant logs and events, and the evidence log path. Keep credentials, kubeconfig, and private keys out of issues. A CSI or host tooling failure should be distinguished from a defect in this repository. Defect fixes belong to later work.
