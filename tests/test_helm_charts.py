"""Rendered Helm manifests are the deployment interface."""

import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HELM = shutil.which("helm")


@unittest.skipUnless(HELM, "Helm is not installed")
class HelmChartsTest(unittest.TestCase):
    def render(self, chart, *values):
        command = [HELM, "template", "test-release", str(ROOT / chart)]
        for value in values:
            command.extend(["--set", value])
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        return result.stdout

    def test_operator_charts_render_deployments_and_bindings(self):
        for chart in ("main-resize-operator/chart", "auto-resize-operator/auto-resize"):
            with self.subTest(chart=chart):
                manifests = self.render(chart, "image.repository=example/operator")
                self.assertIn("kind: Deployment", manifests)
                self.assertIn("kind: ClusterRoleBinding", manifests)
                self.assertNotIn("{ {", manifests)

    def test_webhook_uses_external_secret_and_ca(self):
        manifests = self.render(
            "webhook/rso-webhook-server",
            "image.repository=example/webhook",
            "tls.existingSecret=rotated-webhook-tls",
            "tls.caBundle=Y2E=",
        )
        self.assertIn('secretName: "rotated-webhook-tls"', manifests)
        self.assertIn('caBundle: "Y2E="', manifests)
        self.assertNotIn("kind: Secret", manifests)


if __name__ == "__main__":
    unittest.main()
