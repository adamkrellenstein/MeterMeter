import unittest

from PoetryMeter.llm_sidecar import LLMSidecarManager, SidecarConfig


class _FakeProc:
    def __init__(self):
        self.running = True
        self.terminated = False
        self.killed = False

    def poll(self):
        return None if self.running else 0

    def terminate(self):
        self.terminated = True
        self.running = False

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.killed = True
        self.running = False


class LLMSidecarManagerTests(unittest.TestCase):
    def _config(self, **overrides):
        cfg = SidecarConfig(
            enabled=True,
            binary_path="/tmp/fake-llm-server",
            model_path="/tmp/fake-model.gguf",
            host="127.0.0.1",
            port=11435,
            command_template=("{binary_path}", "--model", "{model_path}", "--port", "{port}"),
            startup_timeout_ms=300,
            stop_timeout_ms=100,
            cooldown_ms=1000,
            healthcheck_path="/v1/models",
            healthcheck_interval_ms=3000,
        )
        return SidecarConfig(**{**cfg.__dict__, **overrides})

    def test_ensure_running_starts_once_and_reuses(self):
        calls = {"count": 0}
        commands = []

        def popen_factory(cmd, stdout=None, stderr=None):
            calls["count"] += 1
            commands.append(cmd)
            return _FakeProc()

        manager = LLMSidecarManager(
            config=self._config(),
            popen_factory=popen_factory,
            health_checker=lambda _url, _timeout: True,
        )

        first = manager.ensure_running()
        second = manager.ensure_running()

        self.assertIsNotNone(first)
        self.assertEqual(first, second)
        self.assertEqual(calls["count"], 1)
        self.assertIn("--model", commands[0])

    def test_restart_relaunches_process(self):
        calls = {"count": 0}

        def popen_factory(_cmd, stdout=None, stderr=None):
            calls["count"] += 1
            return _FakeProc()

        manager = LLMSidecarManager(
            config=self._config(),
            popen_factory=popen_factory,
            health_checker=lambda _url, _timeout: True,
        )

        first = manager.ensure_running()
        second = manager.restart()

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertEqual(calls["count"], 2)

    def test_failure_enters_cooldown(self):
        calls = {"count": 0}

        def popen_factory(_cmd, stdout=None, stderr=None):
            calls["count"] += 1
            return _FakeProc()

        manager = LLMSidecarManager(
            config=self._config(startup_timeout_ms=250, cooldown_ms=800),
            popen_factory=popen_factory,
            health_checker=lambda _url, _timeout: False,
        )

        first = manager.ensure_running()
        second = manager.ensure_running()

        self.assertIsNone(first)
        self.assertIsNone(second)
        self.assertEqual(calls["count"], 1)
        self.assertTrue(manager.last_error)


if __name__ == "__main__":
    unittest.main()
