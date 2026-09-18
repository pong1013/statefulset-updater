# Webhook / 準入 Webhook

[返回主指南](../README.md) · [Back to the main guide](../README.md)

[繁體中文](#繁體中文) · [English](#english)

## 繁體中文

這個 webhook 只處理 **StatefulSet 更新（UPDATE）**。當你要求增加 volumeClaimTemplate 的儲存容量時，它會把模板大小改回原值，並在 StatefulSet 上加入 `resize-statefulset-operator/resize-N` annotation。接著由[手動擴容 operator](../main-resize-operator/README.md)處理 PVC。它不會直接擴容 PVC。

### 安裝前

1. 確認目標 StorageClass 支援 PVC 擴容，並先在測試叢集試用。
2. 取得新的 `server.crt`、`server.key` 與 `ca.crt`。憑證的 SAN 必須包含 `webhook.<namespace>.svc`。產生方式見[憑證指南](cert/README.md)。
3. 把映像推送到叢集可存取的 registry。主指南有[完整建置順序](../README.md#快速開始)。

以下指令都從 **repo 根目錄**執行。替換 registry 與憑證路徑；範例假設映像可公開拉取。若使用私有 registry，請先建立 `regcred`，並移除 Helm 指令中的 `--set-json 'imagePullSecrets=[]'`。

~~~sh
export NAMESPACE=default
export REGISTRY=registry.example.com/team
export TAG=1.0.0
export CERT_DIR="$HOME/.local/share/statefulset-updater/certs"

docker build -f webhook/Dockerfile -t "$REGISTRY/webhook:$TAG" .
docker push "$REGISTRY/webhook:$TAG"

kubectl -n "$NAMESPACE" create secret generic admission-tls \
  --from-file=server.crt="$CERT_DIR/server.crt" \
  --from-file=server.key="$CERT_DIR/server.key" \
  --dry-run=client -o yaml | kubectl apply -f -

CA_BUNDLE=$(base64 < "$CERT_DIR/ca.crt" | tr -d '\n')
helm upgrade --install webhook ./webhook/rso-webhook-server \
  --namespace "$NAMESPACE" \
  --set "image.repository=$REGISTRY/webhook" \
  --set "image.tag=$TAG" \
  --set tls.existingSecret=admission-tls \
  --set-string "tls.caBundle=$CA_BUNDLE" \
  --set-json 'imagePullSecrets=[]'
~~~

### 確認與排查

~~~sh
helm -n "$NAMESPACE" status webhook
kubectl -n "$NAMESPACE" get secret admission-tls
kubectl -n "$NAMESPACE" get deployment,service
kubectl get mutatingwebhookconfigurations
~~~

- 若 Helm 提示缺少 `tls.existingSecret` 或 `tls.caBundle`，先建立 Secret，再傳入 Secret 名稱與 **base64 編碼的 CA 憑證**。
- 若出現 `x509` 錯誤，檢查 server 憑證的 SAN 是否對應 `webhook.$NAMESPACE.svc`，且 CA bundle 是否來自簽發該憑證的 CA。
- 先前有私鑰提交到這個 repo。若叢集使用過它，請輪替金鑰；目前版本不再把私鑰放進 Helm chart。

## English

This webhook handles **StatefulSet UPDATE requests**. When a volumeClaimTemplate size increases, it restores the old template size and adds a `resize-statefulset-operator/resize-N` annotation to the StatefulSet. The [manual resize operator](../main-resize-operator/README.md) then expands the PVC. The webhook does not expand PVCs itself.

### Before installing

1. Confirm that the target StorageClass supports PVC expansion, and try the workflow in a test cluster first.
2. Obtain fresh `server.crt`, `server.key`, and `ca.crt` files. The server certificate SAN must include `webhook.<namespace>.svc`. See the [certificate guide](cert/README.md).
3. Push an image to a registry reachable by the cluster. The [main guide](../README.md#quick-start) gives the full deployment order.

Run these commands from the **repository root**. Replace the registry and certificate path. The example assumes images can be pulled without credentials. For a private registry, create `regcred` and omit `--set-json 'imagePullSecrets=[]'`.

~~~sh
export NAMESPACE=default
export REGISTRY=registry.example.com/team
export TAG=1.0.0
export CERT_DIR="$HOME/.local/share/statefulset-updater/certs"

docker build -f webhook/Dockerfile -t "$REGISTRY/webhook:$TAG" .
docker push "$REGISTRY/webhook:$TAG"

kubectl -n "$NAMESPACE" create secret generic admission-tls \
  --from-file=server.crt="$CERT_DIR/server.crt" \
  --from-file=server.key="$CERT_DIR/server.key" \
  --dry-run=client -o yaml | kubectl apply -f -

CA_BUNDLE=$(base64 < "$CERT_DIR/ca.crt" | tr -d '\n')
helm upgrade --install webhook ./webhook/rso-webhook-server \
  --namespace "$NAMESPACE" \
  --set "image.repository=$REGISTRY/webhook" \
  --set "image.tag=$TAG" \
  --set tls.existingSecret=admission-tls \
  --set-string "tls.caBundle=$CA_BUNDLE" \
  --set-json 'imagePullSecrets=[]'
~~~

### Check and troubleshoot

~~~sh
helm -n "$NAMESPACE" status webhook
kubectl -n "$NAMESPACE" get secret admission-tls
kubectl -n "$NAMESPACE" get deployment,service
kubectl get mutatingwebhookconfigurations
~~~

- If Helm reports missing `tls.existingSecret` or `tls.caBundle`, create the Secret first, then pass its name and the **base64-encoded CA certificate**.
- For an `x509` error, check that the server certificate SAN matches `webhook.$NAMESPACE.svc` and that the CA bundle comes from the CA that signed it.
- A private key was previously committed to this repository. Rotate it if a cluster used it. The current chart no longer contains a private key.
