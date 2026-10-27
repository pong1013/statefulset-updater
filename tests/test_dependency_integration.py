"""Exercise installed Flask and Kubernetes through HTTP application/API seams.

The local HTTP server substitutes only for the external Kubernetes API. SDK
models, serialization, REST transport, Flask routing and error handling are real.
These tests require requirements.txt to be installed; missing libraries are errors.
"""

import base64
import copy
import importlib.util
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from kubernetes import client, config


ROOT = Path(__file__).resolve().parents[1]
STATEFULSET_PATH = "/apis/apps/v1/namespaces/testing/statefulsets/database"
STATEFULSET = {
    "apiVersion": "apps/v1",
    "kind": "StatefulSet",
    "metadata": {"name": "database", "namespace": "testing", "resourceVersion": "7"},
    "spec": {
        "serviceName": "database",
        "selector": {"matchLabels": {"app": "database"}},
        "template": {
            "metadata": {"labels": {"app": "database"}},
            "spec": {"containers": [{"name": "database", "image": "example/database"}]},
        },
        "volumeClaimTemplates": [{
            "metadata": {"name": "data"},
            "spec": {"resources": {"requests": {"storage": "10Gi"}}},
        }],
    },
}


def load_application(relative_path, module_name):
    spec = importlib.util.spec_from_file_location(module_name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    with patch.object(config, "load_kube_config"):
        spec.loader.exec_module(module)
    return module


class InstalledDependencyIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.statefulset = copy.deepcopy(STATEFULSET)
        self.configmaps = {}
        self.calls = []
        test = self

        class KubernetesHandler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def respond(self, status, body):
                payload = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self):
                test.calls.append(("GET", self.path, None))
                if self.path == STATEFULSET_PATH and test.statefulset is not None:
                    self.respond(200, test.statefulset)
                elif self.path in test.configmaps:
                    self.respond(200, test.configmaps[self.path])
                else:
                    self.respond(404, {"kind": "Status", "code": 404, "reason": "NotFound"})

            def do_DELETE(self):
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length)) if length else None
                test.calls.append(("DELETE", self.path, body))
                test.statefulset = None
                self.respond(200, {"kind": "Status", "status": "Success"})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                test.calls.append(("POST", self.path, body))
                if self.path == "/api/v1/namespaces/testing/configmaps":
                    test.configmaps[self.path + "/database-backup"] = body
                elif self.path == "/apis/apps/v1/namespaces/testing/statefulsets":
                    test.statefulset = body
                else:
                    self.respond(404, {"kind": "Status", "code": 404})
                    return
                self.respond(201, body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), KubernetesHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        previous = client.Configuration.get_default_copy()
        configuration = client.Configuration()
        configuration.host = "http://127.0.0.1:" + str(self.server.server_port)
        configuration.proxy = None
        client.Configuration.set_default(configuration)
        self.addCleanup(client.Configuration.set_default, previous)

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def review(self, storage):
        requested = copy.deepcopy(STATEFULSET)
        requested["spec"]["volumeClaimTemplates"][0]["spec"]["resources"]["requests"]["storage"] = storage
        return {
            "apiVersion": "admission.k8s.io/v1", "kind": "AdmissionReview",
            "request": {"uid": "resize-request", "object": requested},
        }

    def test_admission_http_preserves_storage_and_emits_resize_annotation(self):
        webhook = load_application("webhook/webhook_server.py", "installed_webhook")
        with webhook.app.test_client() as http:
            response = http.post("/mutating", json=self.review("20Gi"))
        self.assertEqual(response.status_code, 200)
        review = json.loads(response.get_data(as_text=True))
        self.assertEqual(review["apiVersion"], "admission.k8s.io/v1")
        self.assertEqual(review["response"]["uid"], "resize-request")
        self.assertTrue(review["response"]["allowed"])
        self.assertEqual(review["response"]["patchType"], "JSONPatch")
        self.assertEqual(json.loads(base64.b64decode(review["response"]["patch"])), [
            {"op": "add", "path": "/metadata/annotations", "value": {}},
            {"op": "replace", "path": "/spec/volumeClaimTemplates/0/spec/resources/requests/storage", "value": "10Gi"},
            {"op": "add", "path": "/metadata/annotations/resize-statefulset-operator~1resize-0", "value": "20Gi"},
        ])
        self.assertEqual(self.calls, [("GET", STATEFULSET_PATH, None)])

    def test_admission_http_rejects_shrink_and_oversized_requests(self):
        webhook = load_application("webhook/webhook_server.py", "installed_webhook_errors")
        with webhook.app.test_client() as http:
            for storage, message in [
                ("5Gi", "New storage size must be greater than previous size"),
                ("2001Gi", "Storage size can only be modified by up to 2000 Gi"),
            ]:
                with self.subTest(storage=storage):
                    response = http.post("/mutating", json=self.review(storage))
                    self.assertEqual(response.status_code, 400)
                    self.assertIn(message, response.get_data(as_text=True))

    def test_manual_resize_backup_and_recreation_use_real_sdk_models(self):
        operator = load_application("main-resize-operator/resize_sts_operator.py", "installed_manual_operator")
        with patch.object(operator.time, "sleep"):
            operator.modify_sts_to_configmap("database", "20", "testing", 0)
        self.assertEqual(
            self.statefulset["spec"]["volumeClaimTemplates"][0]["spec"]["resources"]["requests"]["storage"],
            "20Gi",
        )
        self.assertNotIn("resourceVersion", self.statefulset["metadata"])
        self.assertIn(
            ("DELETE", STATEFULSET_PATH + "?propagationPolicy=Orphan", None),
            self.calls,
        )
        self.assertIn(("GET", "/api/v1/namespaces/testing/configmaps/database-backup", None), self.calls)


if __name__ == "__main__":
    unittest.main()
