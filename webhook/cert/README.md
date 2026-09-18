# Webhook 憑證 / Webhook Certificates

[返回 Webhook 指南](../README.md) · [Back to the webhook guide](../README.md)

[繁體中文](#繁體中文) · [English](#english)

## 繁體中文

Webhook Pod 從 Kubernetes Secret 讀取 `server.crt` 和 `server.key`，掛載路徑為 `/certs`。Helm 的 `tls.existingSecret` 指向這個 Secret；`tls.caBundle` 必須是**簽發 server.crt 的 CA 憑證**經 base64 編碼後的內容。伺服器憑證的 SAN 必須包含 `webhook.<namespace>.svc`，因為 chart 建立的 Service 名稱是 `webhook`。

正式環境請使用既有 PKI 或憑證管理工具。以下 OpenSSL 指令只示範如何在測試環境建立一組新的 CA 與伺服器憑證。請在 repo **以外**保管私鑰；`server.key` 必須是不需互動輸入密碼的格式，webhook 才能啟動。

~~~sh
export NAMESPACE=default
export CERT_DIR="$HOME/.local/share/statefulset-updater/certs"
umask 077
mkdir -p "$CERT_DIR"

openssl req -x509 -newkey rsa:4096 -sha256 -days 365 -nodes \
  -keyout "$CERT_DIR/ca.key" -out "$CERT_DIR/ca.crt" \
  -subj "/CN=statefulset-updater-test-ca"

openssl req -newkey rsa:2048 -nodes \
  -keyout "$CERT_DIR/server.key" -out "$CERT_DIR/server.csr" \
  -subj "/CN=webhook.$NAMESPACE.svc"

cat > "$CERT_DIR/server.ext" <<EOF
basicConstraints=CA:FALSE
keyUsage=digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=DNS:webhook.$NAMESPACE.svc,DNS:webhook.$NAMESPACE.svc.cluster.local
EOF

openssl x509 -req -in "$CERT_DIR/server.csr" \
  -CA "$CERT_DIR/ca.crt" -CAkey "$CERT_DIR/ca.key" -CAcreateserial \
  -out "$CERT_DIR/server.crt" -days 365 -sha256 \
  -extfile "$CERT_DIR/server.ext"

openssl verify -CAfile "$CERT_DIR/ca.crt" "$CERT_DIR/server.crt"
openssl x509 -in "$CERT_DIR/server.crt" -noout -ext subjectAltName
~~~

預期 `openssl verify` 輸出 `OK`，SAN 輸出包含正確的 namespace。接著依 [Webhook 指南](../README.md#安裝前)建立 Secret，並把 CA 憑證轉成 Helm 所需的值：

~~~sh
CA_BUNDLE=$(base64 < "$CERT_DIR/ca.crt" | tr -d '\n')
~~~

**重要：**此 repo 曾提交私鑰。若任何叢集使用過該金鑰，必須換發新的憑證與金鑰並更新 Secret 和 Helm 設定。刪除目前工作樹中的檔案不會清除 Git 歷史。更新 Secret 後，請重啟 webhook Deployment，讓程序重新讀取憑證。

## English

The webhook Pod reads `server.crt` and `server.key` from a Kubernetes Secret mounted at `/certs`. Helm's `tls.existingSecret` names that Secret. `tls.caBundle` must contain the **base64-encoded certificate of the CA that signed server.crt**. The server certificate SAN must include `webhook.<namespace>.svc` because the chart creates a Service named `webhook`.

Use your existing PKI or certificate manager in production. The OpenSSL commands below create a new CA and server certificate for a test environment. Keep private keys **outside the repository**. The webhook needs an unencrypted `server.key` so it can start without an interactive passphrase.

~~~sh
export NAMESPACE=default
export CERT_DIR="$HOME/.local/share/statefulset-updater/certs"
umask 077
mkdir -p "$CERT_DIR"

openssl req -x509 -newkey rsa:4096 -sha256 -days 365 -nodes \
  -keyout "$CERT_DIR/ca.key" -out "$CERT_DIR/ca.crt" \
  -subj "/CN=statefulset-updater-test-ca"

openssl req -newkey rsa:2048 -nodes \
  -keyout "$CERT_DIR/server.key" -out "$CERT_DIR/server.csr" \
  -subj "/CN=webhook.$NAMESPACE.svc"

cat > "$CERT_DIR/server.ext" <<EOF
basicConstraints=CA:FALSE
keyUsage=digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=DNS:webhook.$NAMESPACE.svc,DNS:webhook.$NAMESPACE.svc.cluster.local
EOF

openssl x509 -req -in "$CERT_DIR/server.csr" \
  -CA "$CERT_DIR/ca.crt" -CAkey "$CERT_DIR/ca.key" -CAcreateserial \
  -out "$CERT_DIR/server.crt" -days 365 -sha256 \
  -extfile "$CERT_DIR/server.ext"

openssl verify -CAfile "$CERT_DIR/ca.crt" "$CERT_DIR/server.crt"
openssl x509 -in "$CERT_DIR/server.crt" -noout -ext subjectAltName
~~~

`openssl verify` should print `OK`, and the SAN output should include the intended namespace. Then follow the [webhook guide](../README.md#before-installing) to create the Secret and encode the CA certificate for Helm:

~~~sh
CA_BUNDLE=$(base64 < "$CERT_DIR/ca.crt" | tr -d '\n')
~~~

**Important:** A private key was previously committed to this repository. If any cluster used it, issue a new certificate and key and update the Secret and Helm settings. Deleting the file from the current working tree does not remove it from Git history. Restart the webhook Deployment after updating the Secret so the process reloads the certificate.
