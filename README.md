# StatefulSet PVC Resizer / StatefulSet PVC 擴容工具

[繁體中文](#繁體中文) · [English](#english)

## 繁體中文

這個專案協助擴大 Kubernetes StatefulSet 使用的 PersistentVolumeClaim（PVC）。它由三個元件組成；請依照 **webhook → 手動擴容 operator → 自動擴容 operator** 的順序部署。自動擴容是選用功能。

> **先在測試叢集驗證。** 手動 operator 會以 orphan 方式刪除並重建 StatefulSet 控制器，以更新不可直接修改的 volumeClaimTemplates。現有 Pod 會保留，但操作仍可能影響工作負載。請先確認備份、StorageClass 與維運窗口。

### 元件與運作方式

| 元件 | 工作 | 說明 |
| --- | --- | --- |
| [Webhook](webhook/README.md) | 攔截 StatefulSet 的儲存空間增加請求，還原模板大小並加入擴容 annotation | 必要 |
| [手動擴容 operator](main-resize-operator/README.md) | 根據 annotation 擴大 PVC，並重建 StatefulSet | 必要 |
| [自動擴容 operator](auto-resize-operator/README.md) | 掛載磁碟使用率達 80% 時發起擴容請求 | 選用 |

![Webhook 流程](images/webhook.png)

### 開始之前

- 準備可連線的 Kubernetes 叢集、kubectl、Helm 3、Docker，以及可供叢集拉取映像的 registry。
- 確認目標 PVC 的 StorageClass 支援擴容（allowVolumeExpansion 為 true），而且儲存驅動支援線上或離線擴容。
- 準備新的 webhook 伺服器憑證、私鑰和簽發它的 CA。憑證的 SAN 必須包含 `webhook.<namespace>.svc`；參見[憑證指南](webhook/cert/README.md)。
- 以下指令從**此 repo 根目錄**執行。把 `REGISTRY` 改成你自己的 registry；`registry.example.com/team` 只是範例。若使用私有 registry，先設定映像拉取憑證。
- 若使用非 `default` namespace，先建立該 namespace；推送映像前也可能需要先登入 registry。

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

### 快速開始

**1. 建置並推送映像。** 若只使用手動擴容，可以略過第三個映像。

~~~sh
docker build -f webhook/Dockerfile -t "$REGISTRY/webhook:$TAG" .
docker build -f main-resize-operator/Dockerfile -t "$REGISTRY/manual-resize:$TAG" .
docker build -f auto-resize-operator/Dockerfile -t "$REGISTRY/auto-resize:$TAG" .

docker push "$REGISTRY/webhook:$TAG"
docker push "$REGISTRY/manual-resize:$TAG"
docker push "$REGISTRY/auto-resize:$TAG"
~~~

**2. 建立 webhook TLS Secret。** 先依[憑證指南](webhook/cert/README.md)產生或取得檔案，再設定 `CERT_DIR`。Secret 需要 `server.crt` 與 `server.key` 兩個鍵；`ca.crt` 用來設定 webhook 的 CA bundle。

~~~sh
export CERT_DIR="$HOME/.local/share/statefulset-updater/certs"

kubectl -n "$NAMESPACE" create secret generic admission-tls \
  --from-file=server.crt="$CERT_DIR/server.crt" \
  --from-file=server.key="$CERT_DIR/server.key" \
  --dry-run=client -o yaml | kubectl apply -f -

CA_BUNDLE=$(base64 < "$CERT_DIR/ca.crt" | tr -d '\n')
~~~

**3. 依序安裝。** 以下範例假設叢集可以直接拉取映像，所以用 `--set-json 'imagePullSecrets=[]'` 覆蓋 chart 預設的 `regcred`。若使用私有 registry，請建立 `regcred`，並移除這個選項。

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

**4. 確認部署。**

~~~sh
helm -n "$NAMESPACE" list
kubectl -n "$NAMESPACE" get deployments,pods,service
kubectl get mutatingwebhookconfigurations
~~~

### 使用方式

先列出 StatefulSet，並確認要擴容的 PVC：

~~~sh
kubectl -n "$NAMESPACE" get statefulsets
kubectl -n "$NAMESPACE" get pvc
~~~

**手動擴容：** 把 `my-statefulset` 換成實際名稱，`20Gi` 換成**大於目前容量**的目標值。`resize-0` 指第一個 volumeClaimTemplate，`resize-1` 指第二個。

~~~sh
kubectl -n "$NAMESPACE" annotate statefulset my-statefulset \
  resize-statefulset-operator/resize-0=20Gi --overwrite
kubectl -n "$NAMESPACE" get pvc
~~~

**自動擴容：** 先安裝三個元件，再為目標 StatefulSet 加上值為 `true` 的 label。自動 operator 會檢查有掛載的 PVC；使用率達 80% 時提出擴容請求。

~~~sh
kubectl -n "$NAMESPACE" label statefulset my-statefulset \
  resize-statefulset-operator/auto-scaled=true --overwrite
~~~

### 驗證與已知限制

在 repo 根目錄執行：

~~~sh
python3 -m unittest discover -s tests -v
helm lint main-resize-operator/chart --set image.repository=example/operator
helm lint auto-resize-operator/auto-resize --set image.repository=example/operator
helm lint webhook/rso-webhook-server \
  --set image.repository=example/webhook \
  --set tls.existingSecret=example-tls \
  --set tls.caBundle=Y2E=
~~~

- 測試需要 Python 3；Helm 渲染測試只有在本機安裝 Helm 時才會執行。CI 會執行全部檢查。
- 這個專案只擴容，不縮小 PVC。Webhook 目前限制要求的大小不超過 2000Gi。
- 自動 operator 在容器內使用 `df -h --output=...`；目標容器需要支援這個 GNU df 選項。
- 曾有私鑰提交到此 repo。若叢集使用過該金鑰，**必須輪替**；從目前版本刪除檔案並不會清除 Git 歷史。

## English

This project expands PersistentVolumeClaims (PVCs) used by Kubernetes StatefulSets. It has three components. Deploy them in this order: **webhook → manual resize operator → automatic resize operator**. Automatic resizing is optional.

> **Try this in a test cluster first.** The manual operator deletes and recreates the StatefulSet controller with orphan propagation to update its immutable volumeClaimTemplates. Existing Pods remain, but the operation can still affect a workload. Check backups, the StorageClass, and your maintenance window first.

### Components and flow

| Component | Job | Required? |
| --- | --- | --- |
| [Webhook](webhook/README.md) | Intercepts a requested storage increase, restores the old template size, and adds a resize annotation | Yes |
| [Manual resize operator](main-resize-operator/README.md) | Expands the PVCs named by the annotation and recreates the StatefulSet | Yes |
| [Automatic resize operator](auto-resize-operator/README.md) | Requests an increase when a mounted volume reaches 80% usage | Optional |

![Webhook flow](images/webhook.png)

### Prerequisites

- A reachable Kubernetes cluster, kubectl, Helm 3, Docker, and an image registry that the cluster can pull from.
- A StorageClass with `allowVolumeExpansion: true` and a storage driver that supports expansion.
- A fresh webhook server certificate and key, plus its signing CA. The certificate SAN must include `webhook.<namespace>.svc`. See the [certificate guide](webhook/cert/README.md).
- Run the commands below from the **repository root**. Replace `REGISTRY` with your registry; `registry.example.com/team` is only an example. Configure image pull credentials if your registry is private.
- If you use a namespace other than `default`, create it first. You may also need to sign in to your registry before pushing images.

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

### Quick start

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

### Use it

Find the StatefulSet and PVC you want to expand:

~~~sh
kubectl -n "$NAMESPACE" get statefulsets
kubectl -n "$NAMESPACE" get pvc
~~~

**Manual resize:** Replace `my-statefulset` with its name and `20Gi` with a target **larger than the current size**. `resize-0` selects the first volumeClaimTemplate; `resize-1` selects the second.

~~~sh
kubectl -n "$NAMESPACE" annotate statefulset my-statefulset \
  resize-statefulset-operator/resize-0=20Gi --overwrite
kubectl -n "$NAMESPACE" get pvc
~~~

**Automatic resize:** Install all three components, then add a label with the value `true`. The automatic operator checks mounted PVCs and requests an increase at 80% usage.

~~~sh
kubectl -n "$NAMESPACE" label statefulset my-statefulset \
  resize-statefulset-operator/auto-scaled=true --overwrite
~~~

### Verification and limitations

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
- This project expands PVCs; it does not shrink them. The webhook currently rejects requested sizes above 2000Gi.
- The automatic operator runs `df -h --output=...` inside the target container. That container must support this GNU df option.
- A private key was previously committed to this repository. **Rotate it** if any cluster used it. Removing the file from the current tree does not remove it from Git history.
