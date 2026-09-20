# 自動擴容 Operator / Automatic Resize Operator

[返回繁中主指南](../README.zh-TW.md) · [Back to the English guide](../README.md)

[繁體中文](#繁體中文) · [English](#english)

## 繁體中文

這個 operator 只處理標記為 `resize-statefulset-operator/auto-scaled=true` 的 StatefulSet。它會逐一檢查有掛載的 PVC；當 Pod 內該掛載點使用率達 **80%**，就提出更大的 volumeClaimTemplate 請求。後續仍由 [webhook](../webhook/README.md) 加入 annotation，再由[手動擴容 operator](../main-resize-operator/README.md)完成 PVC 擴容。

### 啟用

先依[繁中主指南](../README.zh-TW.md#快速開始)安裝三個元件，再把 `my-statefulset` 換成目標名稱：

~~~sh
export NAMESPACE=default
kubectl -n "$NAMESPACE" label statefulset my-statefulset \
  resize-statefulset-operator/auto-scaled=true --overwrite

kubectl -n "$NAMESPACE" get statefulset my-statefulset --show-labels
kubectl -n "$NAMESPACE" logs deployment/auto-resize
kubectl -n "$NAMESPACE" get pvc
~~~

label 必須是 **true**，不是 annotation。只有在容器中找到對應 volumeMount 的 PVC 才會檢查。容器內需要支援 `df -h --output=...`（GNU df）；若映像只有 BusyBox df，請先確認相容性。擴容只會增加容量，不會縮小 PVC。

### 安裝

以下指令從 repo 根目錄執行。先部署 webhook 與手動 operator，再部署自動 operator。把範例 registry 換成叢集可存取的位址。公開 registry 可用 `--set-json 'imagePullSecrets=[]'`；私有 registry 請建立 `regcred` 並移除該選項。

~~~sh
export NAMESPACE=default
export REGISTRY=registry.example.com/team
export TAG=1.0.0

docker build -f auto-resize-operator/Dockerfile -t "$REGISTRY/auto-resize:$TAG" .
docker push "$REGISTRY/auto-resize:$TAG"

helm upgrade --install auto-resize ./auto-resize-operator/auto-resize \
  --namespace "$NAMESPACE" \
  --set "image.repository=$REGISTRY/auto-resize" \
  --set "image.tag=$TAG" \
  --set-json 'imagePullSecrets=[]'
~~~

若容量沒有變化，先確認 label 值、Pod 的 volumeMount、容器的 df 指令，以及 webhook 和手動 operator 是否正常運作。自動 operator 的錯誤會寫入日誌。

## English

This operator only handles StatefulSets labeled `resize-statefulset-operator/auto-scaled=true`. It checks each mounted PVC. When a mount reaches **80%** usage inside a Pod, it requests a larger volumeClaimTemplate. The [webhook](../webhook/README.md) then adds a resize annotation, and the [manual resize operator](../main-resize-operator/README.md) expands the PVC.

### Enable it

Install all three components using the [main guide](../README.md#quick-start), then replace `my-statefulset` with your StatefulSet name:

~~~sh
export NAMESPACE=default
kubectl -n "$NAMESPACE" label statefulset my-statefulset \
  resize-statefulset-operator/auto-scaled=true --overwrite

kubectl -n "$NAMESPACE" get statefulset my-statefulset --show-labels
kubectl -n "$NAMESPACE" logs deployment/auto-resize
kubectl -n "$NAMESPACE" get pvc
~~~

The label value must be **true**; an annotation does not enable automatic resizing. The operator checks a PVC only if it finds a matching volumeMount in a container. The container must support `df -h --output=...` (GNU df). Check compatibility first if the image only has BusyBox df. Resizing only increases capacity; it never shrinks a PVC.

### Install

Run these commands from the repository root. Install the webhook and manual operator before the automatic operator. Replace the example registry with one your cluster can reach. For a public registry, use `--set-json 'imagePullSecrets=[]'`. For a private registry, create `regcred` and omit that option.

~~~sh
export NAMESPACE=default
export REGISTRY=registry.example.com/team
export TAG=1.0.0

docker build -f auto-resize-operator/Dockerfile -t "$REGISTRY/auto-resize:$TAG" .
docker push "$REGISTRY/auto-resize:$TAG"

helm upgrade --install auto-resize ./auto-resize-operator/auto-resize \
  --namespace "$NAMESPACE" \
  --set "image.repository=$REGISTRY/auto-resize" \
  --set "image.tag=$TAG" \
  --set-json 'imagePullSecrets=[]'
~~~

If the PVC does not change, check the label value, the Pod's volumeMount, the container's df command, and the webhook and manual operator. The automatic operator writes processing errors to its logs.
