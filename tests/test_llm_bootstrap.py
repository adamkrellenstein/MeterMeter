import hashlib
import os
import tempfile
import unittest
from pathlib import Path

from PoetryMeter.llm_bootstrap import BootstrapError, bootstrap_local_llm


class _Settings(dict):
    def get(self, key, default=None):
        return super().get(key, default)


class LLMBootstrapTests(unittest.TestCase):
    def test_uses_existing_paths_without_download(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp) / "llama-server"
            model = Path(tmp) / "model.gguf"
            runtime.write_bytes(b"runtime")
            model.write_bytes(b"model")

            settings = _Settings(
                llm_bootstrap_install_dir=tmp,
                llm_sidecar_binary_path=str(runtime),
                llm_sidecar_model_path=str(model),
            )

            out = bootstrap_local_llm(settings)
            self.assertEqual(out.runtime_path, str(runtime))
            self.assertEqual(out.model_path, str(model))
            self.assertFalse(out.changed)

    def test_downloads_runtime_and_model_from_urls(self):
        with tempfile.TemporaryDirectory() as tmp:
            source_runtime = Path(tmp) / "source-runtime"
            source_model = Path(tmp) / "source-model.gguf"
            source_runtime.write_bytes(b"abc-runtime")
            source_model.write_bytes(b"abc-model")

            runtime_sha = hashlib.sha256(source_runtime.read_bytes()).hexdigest()
            model_sha = hashlib.sha256(source_model.read_bytes()).hexdigest()

            install_dir = Path(tmp) / "install"
            settings = _Settings(
                llm_bootstrap_install_dir=str(install_dir),
                llm_bootstrap_runtime_candidates=[],
                llm_bootstrap_runtime_url=source_runtime.as_uri(),
                llm_bootstrap_runtime_sha256=runtime_sha,
                llm_bootstrap_runtime_filename="llama-server",
                llm_bootstrap_model_url=source_model.as_uri(),
                llm_bootstrap_model_sha256=model_sha,
                llm_bootstrap_model_filename="model.gguf",
                llm_bootstrap_download_timeout_ms=5000,
                llm_bootstrap_overwrite=False,
                llm_sidecar_binary_path="",
                llm_sidecar_model_path="",
            )

            out = bootstrap_local_llm(settings)

            self.assertTrue(Path(out.runtime_path).is_file())
            self.assertTrue(Path(out.model_path).is_file())
            self.assertTrue(out.changed)

            mode = os.stat(out.runtime_path).st_mode
            self.assertTrue(mode & 0o100)

    def test_raises_when_no_runtime_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = _Settings(
                llm_bootstrap_install_dir=tmp,
                llm_bootstrap_runtime_candidates=[],
                llm_bootstrap_runtime_url="",
                llm_bootstrap_model_url="",
                llm_sidecar_binary_path="",
                llm_sidecar_model_path="",
            )

            with self.assertRaises(BootstrapError):
                bootstrap_local_llm(settings)


if __name__ == "__main__":
    unittest.main()
