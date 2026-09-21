# StatefulSet PVC 擴容工具

[English](README.md) · **繁體中文**

這個專案協助擴大 Kubernetes StatefulSet 使用的 PersistentVolumeClaim（PVC）。它由三個元件組成；請依照 **webhook → 手動擴容 operator → 自動擴容 operator** 的順序部署。自動擴容是選用功能。

> **這是跨 namespace 工具。** 元件 Pod 安裝在指定 namespace，但 chart 會建立 cluster-wide 的 `MutatingWebhookConfiguration`、`ClusterRole` 與 `ClusterRoleBinding`。Webhook 會接收所有 namespace 的 StatefulSet 更新，operator 也會監看並可修改所有 namespace 的 StatefulSet、PVC 與備份 ConfigMap。每個 cluster 建議只安裝一套，正式環境使用前請先檢查 RBAC 與適用範圍。

> **先在測試叢集驗證。** 手動 operator 會以 orphan 方式刪除並重建 StatefulSet 控制器，以更新不可直接修改的 volumeClaimTemplates。現有 Pod 會保留，但操作仍可能影響工作負載。請先確認備份、StorageClass 與維運窗口。

## 元件與運作方式

| 元件 | 工作 | 說明 |
| --- | --- | --- |
| [Webhook](webhook/README.md) | 攔截 StatefulSet 的儲存空間增加請求，還原模板大小並加入擴容 annotation | 必要 |
| [手動擴容 operator](main-resize-operator/README.md) | 根據 annotation 擴大 PVC，並重建 StatefulSet | 必要 |
| [自動擴容 operator](auto-resize-operator/README.md) | 掛載磁碟使用率達 80% 時發起擴容請求 | 選用 |

![Webhook 流程](images/webhook.png)

## 開始之前

- 準備目標 Kubernetes cluster 的 kubeconfig，以及 kubectl、Helm 3、Docker 與 Git。
- 確認目標 PVC 的 StorageClass 支援擴容（allowVolumeExpansion 為 true），而且儲存驅動支援線上或離線擴容。
- 請先使用測試 cluster。下方 AI 安裝流程一開始只需要 kubeconfig 路徑；手動安裝才需要另外準備 registry 與新的 webhook TLS 檔案。

## 快速開始

### 使用 AI 安裝

只需要替換 `<KUBECONFIG_PATH>`。AI 會讀取該檔案的 `current-context`，並採用這些預設值：namespace 為 `statefulset-updater`、只安裝必要的 webhook 與手動 operator、image tag 為 `git-<short-commit>`。只有當目標 cluster 無法直接匯入映像，且環境也沒有可用 registry 時，AI 才需要再詢問 registry 資訊。

> 提示詞會授權 AI 建立 cluster-wide RBAC 與 admission webhook。請提供專用的 kubeconfig，並確認其中的 `current-context` 指向預期 cluster。

```text
請把 https://github.com/pong1013/statefulset-updater 安裝到這個 kubeconfig 所設定的 Kubernetes cluster：

- kubeconfig：<KUBECONFIG_PATH>

使用 `statefulset-updater` namespace，只安裝必要的 webhook 與手動 operator；不要安裝選用的自動 operator。

1. 只使用這個 kubeconfig。讀取其中的 `current-context`，顯示 context 與 cluster endpoint；若資訊缺失、有歧義或無法連線就停止。
2. 先說明這是跨 namespace 工具，會建立 cluster-wide webhook 與 RBAC。跨所有 namespace 檢查 Helm release、檢查 `statefulset-updater` 中的 TLS Secret 與 Service，並在 cluster 層級檢查相關 webhook、ClusterRole 和 binding；若已存在，停止並先詢問，不得直接修改。
3. Clone repository 的預設分支，記錄解析後的 commit SHA，並用 `git-<short-commit>` 作為 image tag。檢查節點、可擴容 StorageClass、Helm、Docker 與映像交付方式。只有能匯入至每個可排程節點時才直接匯入映像，並停用 chart 預設的 `regcred`；否則使用環境中已設定的 registry，只有缺少 registry 資訊或憑證時才詢問我。
4. 只建置 webhook 與手動 operator 映像。為 `webhook.statefulset-updater.svc` 和 `webhook.statefulset-updater.svc.cluster.local` 產生新的 TLS，建立 namespace 與 Secret，再依序安裝兩個 chart。可以回報 context 名稱與 server endpoint，但不得顯示或提交 kubeconfig 檔案內容、token、client key、client certificate、exec credential 輸出、registry 憑證、私鑰或 Secret 內容。
5. 驗證 Helm release、Ready Deployment 與 Pod、Service endpoints、MutatingWebhookConfiguration、ClusterRole 和 binding。不得替任何既有 StatefulSet 執行擴容。
6. 回報 commit、context、namespace、映像、cluster-wide 資源、驗證結果、限制與安全移除命令。若失敗就停止並保留診斷資訊。
```

### 手動安裝

以下指令從**此 repo 根目錄**執行。先準備 cluster 可以拉取映像的 registry，以及新的 webhook TLS 檔案。憑證 SAN 必須包含 `webhook.<namespace>.svc`；參見[憑證指南](webhook/cert/README.md)。若不使用 `default` namespace，請先建立；若使用私有 registry，也要設定映像拉取憑證。

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

## 使用方式

先列出 StatefulSet，並確認要擴容的 PVC。`TARGET_NAMESPACE` 是工作負載所在的 namespace，可以不同於元件的安裝 namespace `NAMESPACE`：

~~~sh
export TARGET_NAMESPACE=default
kubectl -n "$TARGET_NAMESPACE" get statefulsets
kubectl -n "$TARGET_NAMESPACE" get pvc
~~~

**手動擴容：** 把 `my-statefulset` 換成實際名稱，`20Gi` 換成**大於目前容量**的目標值。`resize-0` 指第一個 volumeClaimTemplate，`resize-1` 指第二個。

~~~sh
kubectl -n "$TARGET_NAMESPACE" annotate statefulset my-statefulset \
  resize-statefulset-operator/resize-0=20Gi --overwrite
kubectl -n "$TARGET_NAMESPACE" get pvc
~~~

**自動擴容：** 先安裝三個元件，再為目標 StatefulSet 加上值為 `true` 的 label。自動 operator 會檢查有掛載的 PVC；使用率達 80% 時提出擴容請求。

~~~sh
kubectl -n "$TARGET_NAMESPACE" label statefulset my-statefulset \
  resize-statefulset-operator/auto-scaled=true --overwrite
~~~

## 驗證與已知限制

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
- 這個工具預設跨 namespace 運作，會建立 cluster-wide admission 與 RBAC 資源。安裝到某個 namespace 不會把處理範圍限制在該 namespace。
- 這個專案只擴容，不縮小 PVC。Webhook 目前限制要求的大小不超過 2000Gi。
- 自動 operator 在容器內使用 `df -h --output=...`；目標容器需要支援這個 GNU df 選項。
- 曾有私鑰提交到此 repo。若叢集使用過該金鑰，**必須輪替**；從目前版本刪除檔案並不會清除 Git 歷史。
