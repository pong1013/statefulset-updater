# 手動擴容 Operator / Manual Resize Operator

[返回繁中主指南](../README.zh-TW.md) · [Back to the English guide](../README.md)

[繁體中文](#繁體中文) · [English](#english)

## 繁體中文

這個 operator 監看所有 namespace 中的 StatefulSet。你在 StatefulSet 上加入擴容 annotation 後，它會更新對應的 PVC，將 StatefulSet 資訊暫存到 ConfigMap，然後以 orphan 方式刪除並重建 StatefulSet，讓 volumeClaimTemplates 反映新容量。原有 Pod 不會因為刪除控制器而直接刪除，但仍請先在測試叢集驗證。

### 指定要擴容的 PVC

annotation 格式為 `resize-statefulset-operator/resize-N=20Gi`：

- `N` 是 volumeClaimTemplates 中從 **0 開始**的索引。第一個是 `resize-0`，第二個是 `resize-1`。
- 容量目前請使用整數 `Gi`，且必須大於原本大小。此工具不支援縮容。
- 若 StatefulSet 有多個 PVC 模板，可以各自加入對應的 annotation。

先確認模板順序，再提交擴容請求。把 `my-statefulset` 與 `20Gi` 換成你的值：

~~~sh
export NAMESPACE=default
kubectl -n "$NAMESPACE" get statefulsets
kubectl -n "$NAMESPACE" get statefulset my-statefulset \
  -o jsonpath='{.spec.volumeClaimTemplates[*].metadata.name}'
kubectl -n "$NAMESPACE" get pvc

kubectl -n "$NAMESPACE" annotate statefulset my-statefulset \
  resize-statefulset-operator/resize-0=20Gi --overwrite
~~~

操作後查看 PVC 和 operator 日誌：

~~~sh
kubectl -n "$NAMESPACE" get pvc
kubectl -n "$NAMESPACE" get statefulset my-statefulset
kubectl -n "$NAMESPACE" logs deployment/resize-statefulset-operator
~~~

### 安裝

請先完成[繁中主指南的前置檢查](../README.zh-TW.md#開始之前)，尤其是 StorageClass 的 `allowVolumeExpansion`。從 repo 根目錄執行以下指令；把範例 registry 換成叢集可存取的位址。公開 registry 可用 `--set-json 'imagePullSecrets=[]'`；私有 registry 請建立 `regcred` 並移除該選項。

~~~sh
export NAMESPACE=default
export REGISTRY=registry.example.com/team
export TAG=1.0.0

docker build -f main-resize-operator/Dockerfile -t "$REGISTRY/manual-resize:$TAG" .
docker push "$REGISTRY/manual-resize:$TAG"

helm upgrade --install resize-statefulset-operator ./main-resize-operator/chart \
  --namespace "$NAMESPACE" \
  --set "image.repository=$REGISTRY/manual-resize" \
  --set "image.tag=$TAG" \
  --set-json 'imagePullSecrets=[]'
~~~

如果 PVC 沒有擴大，先確認 StorageClass、annotation 索引與容量格式，再看 operator 日誌。StatefulSet 重建期間避免同時修改同一個 StatefulSet。

## English

This operator watches StatefulSets in all namespaces. After you add a resize annotation, it expands the matching PVCs, saves the StatefulSet in a ConfigMap, and deletes and recreates the StatefulSet with orphan propagation so its volumeClaimTemplates show the new size. Existing Pods are not directly deleted with the controller, but test the workflow in a nonproduction cluster first.

### Choose the PVC to expand

The annotation format is `resize-statefulset-operator/resize-N=20Gi`:

- `N` is a **zero-based** index into volumeClaimTemplates. Use `resize-0` for the first template and `resize-1` for the second.
- Currently use a whole number of `Gi`. The target must be larger than the current size. Shrinking is not supported.
- For multiple claim templates, add an annotation for each one you want to expand.

Inspect the template order, then request the increase. Replace `my-statefulset` and `20Gi` with your values:

~~~sh
export NAMESPACE=default
kubectl -n "$NAMESPACE" get statefulsets
kubectl -n "$NAMESPACE" get statefulset my-statefulset \
  -o jsonpath='{.spec.volumeClaimTemplates[*].metadata.name}'
kubectl -n "$NAMESPACE" get pvc

kubectl -n "$NAMESPACE" annotate statefulset my-statefulset \
  resize-statefulset-operator/resize-0=20Gi --overwrite
~~~

Check the PVCs and operator logs afterward:

~~~sh
kubectl -n "$NAMESPACE" get pvc
kubectl -n "$NAMESPACE" get statefulset my-statefulset
kubectl -n "$NAMESPACE" logs deployment/resize-statefulset-operator
~~~

### Install

Complete the [prerequisites in the main guide](../README.md#prerequisites), especially StorageClass `allowVolumeExpansion`. Run these commands from the repository root and replace the example registry with one your cluster can reach. For a public registry, use `--set-json 'imagePullSecrets=[]'`. For a private registry, create `regcred` and omit that option.

~~~sh
export NAMESPACE=default
export REGISTRY=registry.example.com/team
export TAG=1.0.0

docker build -f main-resize-operator/Dockerfile -t "$REGISTRY/manual-resize:$TAG" .
docker push "$REGISTRY/manual-resize:$TAG"

helm upgrade --install resize-statefulset-operator ./main-resize-operator/chart \
  --namespace "$NAMESPACE" \
  --set "image.repository=$REGISTRY/manual-resize" \
  --set "image.tag=$TAG" \
  --set-json 'imagePullSecrets=[]'
~~~

If the PVC does not grow, check the StorageClass, annotation index, and size format, then inspect the operator logs. Avoid editing the same StatefulSet while it is being recreated.
