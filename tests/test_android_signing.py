"""Exercise signing orchestration without a real SDK or private signing key."""

import base64
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "services/frontend/scripts/sign-android-release.sh"


class AndroidSigningTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        tools = self.root / "sdk/build-tools/35.0.0"
        tools.mkdir(parents=True)
        (self.root / "private").mkdir()
        # The doubles validate ordering, decoded key and password indirection.
        for name, body in {
            "zipalign": """
if [[ "$1" == "-c" ]]; then
  [[ "$(<"${@: -1}")" == signed ]]
else
  cp "${@: -2:1}" "${@: -1}"
fi
""",
            "apksigner": """
if [[ "$1" == verify ]]; then
  [[ "$(<"${@: -1}")" == signed ]]
  [[ "${FAIL_VERIFY:-}" != 1 ]]
else
  [[ "$2" == --ks && "$(<"$3")" == fake-key ]]
  [[ "$6" == --ks-pass && "$7" == env:ANDROID_KEYSTORE_PASSWORD ]]
  [[ "$8" == --key-pass && "$9" == env:ANDROID_KEY_PASSWORD ]]
  printf signed > "${11}"
fi
""",
        }.items():
            tool = tools / name
            tool.write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + body)
            tool.chmod(0o700)
        self.env = {
            **os.environ,
            "ANDROID_SDK_ROOT": str(self.root / "sdk"),
            "TMPDIR": str(self.root / "private"),
            "ANDROID_KEYSTORE_BASE64": base64.b64encode(b"fake-key").decode(),
            "ANDROID_KEYSTORE_PASSWORD": "test-store-password",
            "ANDROID_KEY_ALIAS": "test-alias",
            "ANDROID_KEY_PASSWORD": "test-key-password",
        }
        self.source = self.root / "unsigned.apk"
        self.source.write_text("unsigned")
        self.output = self.root / "out/release.apk"

    def run_signer(self):
        result = subprocess.run(  # noqa: S603 — fixed repository script and test-owned paths
            ["/bin/bash", str(SCRIPT), str(self.source), str(self.output)],
            env=self.env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(list((self.root / "private").iterdir()), [])
        self.assertNotIn("test-store-password", result.stdout + result.stderr)
        self.assertNotIn("test-key-password", result.stdout + result.stderr)
        return result

    def test_signs_verifies_and_cleans_private_key(self):
        result = self.run_signer()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.output.read_text(), "signed")

    def test_missing_secret_fails_without_artifact(self):
        self.env.pop("ANDROID_KEY_PASSWORD")
        result = self.run_signer()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Missing signing secret: ANDROID_KEY_PASSWORD", result.stderr)
        self.assertFalse(self.output.exists())

    def test_invalid_base64_cleans_private_key(self):
        self.env["ANDROID_KEYSTORE_BASE64"] = "not valid base64!"
        self.assertNotEqual(self.run_signer().returncode, 0)
        self.assertFalse(self.output.exists())

    def test_failed_verification_never_publishes_apk(self):
        self.env["FAIL_VERIFY"] = "1"
        self.assertNotEqual(self.run_signer().returncode, 0)
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
