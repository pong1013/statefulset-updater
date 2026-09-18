"""AdmissionReview behavior at the Kubernetes webhook boundary."""

import base64
import importlib.util
import json
import logging
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


WEBHOOK = Path(__file__).resolve().parents[1] / "webhook" / "webhook_server.py"


def load_webhook(admission_review):
    previous = types.SimpleNamespace(
        spec=types.SimpleNamespace(
            volume_claim_templates=[
                types.SimpleNamespace(
                    spec=types.SimpleNamespace(
                        resources=types.SimpleNamespace(requests={"storage": "10Gi"})
                    )
                )
            ]
        )
    )
    apps_api = types.SimpleNamespace(
        read_namespaced_stateful_set=lambda name, namespace: previous
    )
    client = types.ModuleType("kubernetes.client")
    client.AppsV1Api = lambda: apps_api
    client.CoreV1Api = lambda: object()
    config = types.ModuleType("kubernetes.config")
    config.load_kube_config = lambda *_: None
    config.load_incluster_config = lambda: None
    watch = types.ModuleType("kubernetes.watch")
    kubernetes = types.ModuleType("kubernetes")
    kubernetes.client, kubernetes.config, kubernetes.watch = client, config, watch

    class FakeFlask:
        def __init__(self, *_args):
            self.logger = logging.getLogger("webhook-admission-test")
            self.logger.disabled = True

        def route(self, *_args, **_kwargs):
            return lambda function: function

    flask = types.ModuleType("flask")
    flask.Flask = FakeFlask
    flask.request = types.SimpleNamespace(get_json=lambda: admission_review)
    flask.abort = lambda *_args: None
    modules = {
        "kubernetes": kubernetes,
        "kubernetes.client": client,
        "kubernetes.config": config,
        "kubernetes.watch": watch,
        "flask": flask,
        "requests": types.ModuleType("requests"),
    }
    spec = importlib.util.spec_from_file_location("webhook_under_test", WEBHOOK)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module


class AdmissionReviewTest(unittest.TestCase):
    def test_resize_request_preserves_existing_annotations(self):
        review = {
            "request": {
                "uid": "request-2",
                "object": {
                    "metadata": {
                        "name": "database", "namespace": "production",
                        "annotations": {"team": "storage"},
                    },
                    "spec": {
                        "volumeClaimTemplates": [
                            {"spec": {"resources": {"requests": {"storage": "20Gi"}}}}
                        ]
                    },
                },
            }
        }
        webhook = load_webhook(review)
        response = json.loads(webhook.mutate_statefulset())
        operations = json.loads(base64.b64decode(response["response"]["patch"]))

        self.assertEqual(
            [operation["path"] for operation in operations],
            [
                "/spec/volumeClaimTemplates/0/spec/resources/requests/storage",
                "/metadata/annotations/resize-statefulset-operator~1resize-0",
            ],
        )

    def test_resize_request_without_annotations_gets_a_valid_parent_path(self):
        review = {
            "request": {
                "uid": "request-1",
                "object": {
                    "metadata": {"name": "database", "namespace": "production"},
                    "spec": {
                        "volumeClaimTemplates": [
                            {"spec": {"resources": {"requests": {"storage": "20Gi"}}}}
                        ]
                    },
                },
            }
        }
        webhook = load_webhook(review)
        response = json.loads(webhook.mutate_statefulset())
        operations = json.loads(base64.b64decode(response["response"]["patch"]))

        self.assertEqual(response["response"]["uid"], "request-1")
        self.assertEqual(
            operations,
            [
                {
                    "op": "add",
                    "path": "/metadata/annotations",
                    "value": {},
                },
                {
                    "op": "replace",
                    "path": "/spec/volumeClaimTemplates/0/spec/resources/requests/storage",
                    "value": "10Gi",
                },
                {
                    "op": "add",
                    "path": "/metadata/annotations/resize-statefulset-operator~1resize-0",
                    "value": "20Gi",
                },
            ],
        )


if __name__ == "__main__":
    unittest.main()
