# StatefulSet PVC Resizer

**English** · [繁體中文](README.zh-TW.md)

This project expands PersistentVolumeClaims (PVCs) used by Kubernetes StatefulSets. It has three components. Deploy them in this order: **webhook → manual resize operator → automatic resize operator**. Automatic resizing is optional.

> **This is a cross-namespace tool.** Its Pods run in the installation namespace, but the charts create a cluster-wide `MutatingWebhookConfiguration`, `ClusterRole`, and `ClusterRoleBinding`. The webhook receives StatefulSet updates from every namespace, and the operators can watch and modify StatefulSets, PVCs, and backup ConfigMaps across namespaces. Install only one instance per cluster and review its RBAC and scope before production use.

> **Try this in a test cluster first.** The manual operator deletes and recreates the StatefulSet controller with orphan propagation to update its immutable volumeClaimTemplates. Existing Pods remain, but the operation can still affect a workload. Check backups, the StorageClass, and your maintenance window first.

## Components and flow

| Component | Job | Required? |
| --- | --- | --- |
| [Webhook](webhook/README.md) | Intercepts a requested storage increase, restores the old template size, and adds a resize annotation | Yes |
| [Manual resize operator](main-resize-operator/README.md) | Expands the PVCs named by the annotation and recreates the StatefulSet | Yes |
| [Automatic resize operator](auto-resize-operator/README.md) | Requests an increase when a mounted volume reaches 80% usage | Optional |

![Webhook flow](images/webhook.png)

## Prerequisites

- A kubeconfig for the intended Kubernetes cluster, plus kubectl, Helm 3, Docker, and Git.
- A StorageClass with `allowVolumeExpansion: true` and a storage driver that supports expansion.
- Use a test cluster first. The AI path below initially needs only the kubeconfig path; manual installation also needs a registry and fresh webhook TLS files.

## Quick start

### Install with AI

Only replace `<KUBECONFIG_PATH>`. The AI reads that file's `current-context` and uses these defaults: namespace `statefulset-updater`, required webhook and manual operator only, and image tag `git-<short-commit>`. It asks for a registry only when the target cluster cannot accept locally imported images and no usable registry is already configured.

> This prompt authorizes cluster-wide RBAC and an admission webhook. Use a dedicated kubeconfig whose `current-context` points to the intended cluster.

```text
Install https://github.com/pong1013/statefulset-updater on the Kubernetes cluster configured by this file:

- kubeconfig: <KUBECONFIG_PATH>

Use namespace `statefulset-updater`. Install the required webhook and manual operator; leave the optional automatic operator disabled.

1. Use only this kubeconfig file. Read its `current-context`, show me the context and cluster endpoint, and stop if either is missing, ambiguous, or unreachable.
2. Explain that this tool works across namespaces and creates cluster-wide webhook and RBAC resources. Check Helm releases across all namespaces, the TLS Secret and Service in `statefulset-updater`, and matching webhook, ClusterRoles, and bindings cluster-wide. Stop and ask before changing anything that already exists.
3. Clone the repository's default branch, record the resolved commit SHA, and use `git-<short-commit>` as the image tag. Check the nodes, an expansion-capable StorageClass, Helm, Docker, and the image delivery path. Import images directly only when they can be loaded onto every schedulable node; in that case disable the charts' default `regcred`. Otherwise use an already configured registry and ask me only if registry information or credentials are missing.
4. Build only the webhook and manual operator images. Generate fresh webhook TLS for `webhook.statefulset-updater.svc` and `webhook.statefulset-updater.svc.cluster.local`, create the namespace and Secret, then install both charts in order. The context name and server endpoint may be reported, but never expose or commit kubeconfig file contents, tokens, client keys or certificates, exec credential output, registry credentials, private keys, or Secret contents.
5. Verify the Helm releases, Ready Deployments and Pods, Service endpoints, MutatingWebhookConfiguration, ClusterRoles, and bindings. Do not resize any existing StatefulSet.
6. Report the commit, context, namespace, images, cluster-wide resources, verification result, limitations, and safe uninstall commands. Stop on failure and retain diagnostics.
```

### Manual installation

Run these commands from the **repository root**. Prepare an image registry that the cluster can pull from and fresh webhook TLS files. The certificate SAN must include `webhook.<namespace>.svc`; see the [certificate guide](webhook/cert/README.md). Create a non-default namespace before installation and configure image pull credentials when using a private registry.

~~~sh
export NAMESPACE=default
export REGISTRY=registry.example.com/team
export TAG=1.0.0

kubectl config current-context
kubectl get nodes
kubectl get storageclass -o custom-columns=NAME:.metadata.name,EXPAND:.allowVolumeExpansion
helm version --short
docker version
~~~

**1. Build and push the images.** You can skip the third image if you only want manual resizing.

~~~sh
docker build -f webhook/Dockerfile -t "$REGISTRY/webhook:$TAG" .
docker build -f main-resize-operator/Dockerfile -t "$REGISTRY/manual-resize:$TAG" .
docker build -f auto-resize-operator/Dockerfile -t "$REGISTRY/auto-resize:$TAG" .

docker push "$REGISTRY/webhook:$TAG"
docker push "$REGISTRY/manual-resize:$TAG"
docker push "$REGISTRY/auto-resize:$TAG"
~~~

**2. Create the webhook TLS Secret.** First obtain the files using the [certificate guide](webhook/cert/README.md), then set `CERT_DIR`. The Secret needs keys named `server.crt` and `server.key`. The `ca.crt` file supplies the webhook CA bundle.

~~~sh
export CERT_DIR="$HOME/.local/share/statefulset-updater/certs"

kubectl -n "$NAMESPACE" create secret generic admission-tls \
  --from-file=server.crt="$CERT_DIR/server.crt" \
  --from-file=server.key="$CERT_DIR/server.key" \
  --dry-run=client -o yaml | kubectl apply -f -

CA_BUNDLE=$(base64 < "$CERT_DIR/ca.crt" | tr -d '\n')
~~~

**3. Install in order.** These examples assume the cluster can pull the images without credentials, so `--set-json 'imagePullSecrets=[]'` overrides the charts' default `regcred`. For a private registry, create `regcred` and omit that option.

~~~sh
helm upgrade --install webhook ./webhook/rso-webhook-server \
  --namespace "$NAMESPACE" \
  --set "image.repository=$REGISTRY/webhook" \
  --set "image.tag=$TAG" \
  --set tls.existingSecret=admission-tls \
  --set-string "tls.caBundle=$CA_BUNDLE" \
  --set-json 'imagePullSecrets=[]'

helm upgrade --install resize-statefulset-operator ./main-resize-operator/chart \
  --namespace "$NAMESPACE" \
  --set "image.repository=$REGISTRY/manual-resize" \
  --set "image.tag=$TAG" \
  --set-json 'imagePullSecrets=[]'

helm upgrade --install auto-resize ./auto-resize-operator/auto-resize \
  --namespace "$NAMESPACE" \
  --set "image.repository=$REGISTRY/auto-resize" \
  --set "image.tag=$TAG" \
  --set-json 'imagePullSecrets=[]'
~~~

**4. Check the installation.**

~~~sh
helm -n "$NAMESPACE" list
kubectl -n "$NAMESPACE" get deployments,pods,service
kubectl get mutatingwebhookconfigurations
~~~

## Use it

Find the StatefulSet and PVC you want to expand. `TARGET_NAMESPACE` is the workload namespace and can differ from the component installation namespace in `NAMESPACE`:

~~~sh
export TARGET_NAMESPACE=default
kubectl -n "$TARGET_NAMESPACE" get statefulsets
kubectl -n "$TARGET_NAMESPACE" get pvc
~~~

**Manual resize:** Replace `my-statefulset` with its name and `20Gi` with a target **larger than the current size**. `resize-0` selects the first volumeClaimTemplate; `resize-1` selects the second.

~~~sh
kubectl -n "$TARGET_NAMESPACE" annotate statefulset my-statefulset \
  resize-statefulset-operator/resize-0=20Gi --overwrite
kubectl -n "$TARGET_NAMESPACE" get pvc
~~~

**Automatic resize:** Install all three components, then add a label with the value `true`. The automatic operator checks mounted PVCs and requests an increase at 80% usage.

~~~sh
kubectl -n "$TARGET_NAMESPACE" label statefulset my-statefulset \
  resize-statefulset-operator/auto-scaled=true --overwrite
~~~

## Verification and limitations

Run these commands from the repository root:

~~~sh
python3 -m unittest discover -s tests -v
helm lint main-resize-operator/chart --set image.repository=example/operator
helm lint auto-resize-operator/auto-resize --set image.repository=example/operator
helm lint webhook/rso-webhook-server \
  --set image.repository=example/webhook \
  --set tls.existingSecret=example-tls \
  --set tls.caBundle=Y2E=
~~~

- Tests need Python 3. Helm rendering tests run locally only when Helm is installed; CI runs the full checks.
- This tool operates across namespaces by default and creates cluster-wide admission and RBAC resources. Installing its Pods in one namespace does not restrict its processing scope to that namespace.
- This project expands PVCs; it does not shrink them. The webhook currently rejects requested sizes above 2000Gi.
- The automatic operator runs `df -h --output=...` inside the target container. That container must support this GNU df option.
- A private key was previously committed to this repository. **Rotate it** if any cluster used it. Removing the file from the current tree does not remove it from Git history.
