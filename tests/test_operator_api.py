"""Behavior checks at the Kubernetes API boundary."""

import base64
import importlib.util
import pickle
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


class ApiException(Exception):
    def __init__(self, status):
        self.status = status


def load_operator(path, apps_api, core_api, stream_func=None):
    client = types.ModuleType("kubernetes.client")
    client.AppsV1Api = lambda: apps_api
    client.CoreV1Api = lambda: core_api
    client.rest = types.SimpleNamespace(ApiException=ApiException)
    client_api = types.ModuleType("kubernetes.client.api")
    client_api.core_v1_api = types.SimpleNamespace()
    client.api = client_api
    config = types.ModuleType("kubernetes.config")
    config.load_kube_config = lambda *_: None
    config.load_incluster_config = lambda: None
    watch = types.ModuleType("kubernetes.watch")
    kubernetes = types.ModuleType("kubernetes")
    kubernetes.client = client
    kubernetes.config = config
    kubernetes.watch = watch
    stream_module = types.ModuleType("kubernetes.stream")
    stream_module.stream = stream_func or (lambda *_args, **_kwargs: None)
    kubernetes.stream = stream_module
    modules = {
        "kubernetes": kubernetes,
        "kubernetes.client": client,
        "kubernetes.client.api": client_api,
        "kubernetes.config": config,
        "kubernetes.watch": watch,
        "kubernetes.stream": stream_module,
    }
    spec = importlib.util.spec_from_file_location("operator_under_test", path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module


class StatefulSetRecreationTest(unittest.TestCase):
    def test_resizes_the_template_named_by_a_sparse_annotation(self):
        class EndWatch(Exception):
            pass

        def template(name):
            return types.SimpleNamespace(
                metadata=types.SimpleNamespace(name=name),
                spec=types.SimpleNamespace(
                    resources=types.SimpleNamespace(requests={"storage": "10Gi"})
                ),
            )

        statefulset = types.SimpleNamespace(
            metadata=types.SimpleNamespace(resource_version="1"),
            spec=types.SimpleNamespace(volume_claim_templates=[template("logs"), template("data")]),
        )
        event = {
            "type": "MODIFIED",
            "object": types.SimpleNamespace(
                kind="StatefulSet",
                metadata=types.SimpleNamespace(
                    name="database", namespace="production", resource_version="1",
                    annotations={"resize-statefulset-operator/resize-1": "20Gi"},
                ),
                spec=statefulset.spec,
                status=types.SimpleNamespace(replicas=1),
            )
        }
        statefulset.metadata.annotations = event["object"].metadata.annotations

        class Watch:
            calls = 0

            def stream(self, *_args, **_kwargs):
                self.calls += 1
                if self.calls == 1:
                    return [event]
                raise EndWatch

        class AppsApi:
            def __init__(self):
                self.statefulset = statefulset

            def list_stateful_set_for_all_namespaces(self):
                return None

            def read_namespaced_stateful_set(self, name, namespace):
                if (name, namespace) != ("database", "production"):
                    raise AssertionError("unexpected StatefulSet")
                if self.statefulset is None:
                    raise ApiException(404)
                return self.statefulset

            def delete_namespaced_stateful_set(self, name, namespace, **_kwargs):
                self.statefulset = None

            def create_namespaced_stateful_set(self, namespace, body):
                self.statefulset = body

        class CoreApi:
            def __init__(self):
                self.pvc = types.SimpleNamespace(
                    spec=types.SimpleNamespace(
                        resources=types.SimpleNamespace(requests={"storage": "10Gi"})
                    )
                )
                self.configmap = None

            def read_namespaced_persistent_volume_claim(self, name, namespace):
                if (name, namespace) != ("data-database-0", "production"):
                    raise AssertionError("wrong PVC selected")
                return self.pvc

            def replace_namespaced_persistent_volume_claim(self, name, namespace, body):
                self.pvc = body

            def create_namespaced_config_map(self, namespace, body):
                self.configmap = body

            def read_namespaced_config_map(self, name, namespace):
                return self.configmap

        apps_api, core_api = AppsApi(), CoreApi()
        module = load_operator(
            ROOT / "main-resize-operator" / "resize_sts_operator.py", apps_api, core_api
        )
        module.watch.Watch = Watch
        module.client.V1ObjectMeta = lambda name: types.SimpleNamespace(name=name)
        module.client.V1ConfigMap = lambda metadata, data: types.SimpleNamespace(
            metadata=metadata, data=data
        )
        with patch.object(module.time, "sleep", lambda *_: None):
            with self.assertRaises(EndWatch):
                module.main()

        self.assertEqual(core_api.pvc.spec.resources.requests["storage"], "20Gi")
        self.assertEqual(
            apps_api.statefulset.spec.volume_claim_templates[0].spec.resources.requests[
                "storage"
            ],
            "10Gi",
        )

    def test_ignores_statefulsets_without_resize_annotations(self):
        class EndWatch(Exception):
            pass

        event = {
            "object": types.SimpleNamespace(
                metadata=types.SimpleNamespace(
                    name="database", namespace="production", resource_version="1",
                    annotations=None,
                )
            )
        }

        class Watch:
            def __init__(self):
                self.calls = 0

            def stream(self, *_args, **_kwargs):
                self.calls += 1
                if self.calls == 1:
                    return [event]
                raise EndWatch

        module = load_operator(
            ROOT / "main-resize-operator" / "resize_sts_operator.py",
            types.SimpleNamespace(list_stateful_set_for_all_namespaces=lambda: None),
            object(),
        )
        module.watch.Watch = Watch
        with patch.object(module.time, "sleep", lambda *_: None):
            with self.assertRaises(EndWatch):
                module.main()

    def test_recreates_statefulset_after_deletion(self):
        statefulset = types.SimpleNamespace(
            spec=types.SimpleNamespace(
                volume_claim_templates=[
                    types.SimpleNamespace(
                        spec=types.SimpleNamespace(
                            resources=types.SimpleNamespace(requests={"storage": "20Gi"})
                        )
                    )
                ]
            )
        )
        stored = base64.b64encode(pickle.dumps(statefulset)).decode("ascii")

        class AppsApi:
            def __init__(self):
                self.statefulset = None

            def delete_namespaced_stateful_set(self, name, namespace, **kwargs):
                self.assert_identity(name, namespace)

            def read_namespaced_stateful_set(self, name, namespace):
                self.assert_identity(name, namespace)
                if self.statefulset is None:
                    raise ApiException(404)
                return self.statefulset

            def create_namespaced_stateful_set(self, namespace, body):
                if namespace != "production":
                    raise AssertionError("unexpected namespace")
                self.statefulset = body

            @staticmethod
            def assert_identity(name, namespace):
                if (name, namespace) != ("database", "production"):
                    raise AssertionError("StatefulSet name and namespace are reversed")

        class CoreApi:
            @staticmethod
            def read_namespaced_config_map(name, namespace):
                if (name, namespace) != ("database-backup", "production"):
                    raise AssertionError("unexpected ConfigMap")
                return types.SimpleNamespace(data={"statefulset": stored})

        apps_api = AppsApi()
        module = load_operator(
            ROOT / "main-resize-operator" / "resize_sts_operator.py", apps_api, CoreApi()
        )
        with patch.object(module.time, "sleep", lambda *_: None):
            module.update_statefulset("database", "database-backup", "production", 0)

        self.assertEqual(
            apps_api.statefulset.spec.volume_claim_templates[0].spec.resources.requests[
                "storage"
            ],
            "20Gi",
        )


class AutomaticResizeTest(unittest.TestCase):
    def test_resizes_a_full_second_volume(self):
        class EndWatch(Exception):
            pass

        def template(name):
            return types.SimpleNamespace(
                metadata=types.SimpleNamespace(name=name),
                spec=types.SimpleNamespace(
                    resources=types.SimpleNamespace(requests={"storage": "10Gi"})
                ),
            )

        container = types.SimpleNamespace(
            name="worker",
            volume_mounts=[
                types.SimpleNamespace(name="logs", mount_path="/logs"),
                types.SimpleNamespace(name="data", mount_path="/data"),
            ],
        )
        statefulset = types.SimpleNamespace(
            metadata=types.SimpleNamespace(
                name="database", namespace="production", resource_version="1",
                labels={"resize-statefulset-operator/auto-scaled": "true"},
            ),
            spec=types.SimpleNamespace(
                volume_claim_templates=[template("logs"), template("data")],
                template=types.SimpleNamespace(
                    spec=types.SimpleNamespace(containers=[container])
                ),
            ),
            status=types.SimpleNamespace(replicas=1),
        )

        class Watch:
            calls = 0

            def stream(self, *_args, **_kwargs):
                self.calls += 1
                if self.calls == 1:
                    return [{"object": statefulset}]
                raise EndWatch

        class AppsApi:
            patched = None

            def list_stateful_set_for_all_namespaces(self):
                return None

            def read_namespaced_stateful_set(self, name, namespace):
                return statefulset

            def patch_namespaced_stateful_set(self, name, namespace, body):
                self.patched = body

        class CoreApi:
            def read_namespaced_pod(self, name, namespace):
                return types.SimpleNamespace(
                    metadata=types.SimpleNamespace(name=name, namespace=namespace),
                    spec=types.SimpleNamespace(containers=[container]),
                )

            def connect_get_namespaced_pod_exec(self):
                return None

        def pod_exec(_api_call, **kwargs):
            command = kwargs["command"][2]
            if "--output=size" in command:
                return "9.8G\n"
            return "90%\n" if "/data" in command else "50%\n"

        apps_api = AppsApi()
        module = load_operator(
            ROOT / "auto-resize-operator" / "auto.py", apps_api, CoreApi(), pod_exec
        )
        module.watch.Watch = Watch
        with self.assertRaises(EndWatch):
            module.main()

        self.assertIsNotNone(apps_api.patched)
        self.assertEqual(
            apps_api.patched.spec.volume_claim_templates[1].spec.resources.requests[
                "storage"
            ],
            "16Gi",
        )

        statefulset.metadata.labels["resize-statefulset-operator/auto-scaled"] = "false"
        apps_api.patched = None
        with self.assertRaises(EndWatch):
            module.main()
        self.assertIsNone(apps_api.patched)


if __name__ == "__main__":
    unittest.main()
