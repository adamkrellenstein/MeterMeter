import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Tuple

HealthCheckFn = Callable[[str, float], bool]


@dataclass(frozen=True)
class SidecarConfig:
    enabled: bool
    binary_path: str
    model_path: str
    host: str
    port: int
    command_template: Tuple[str, ...]
    startup_timeout_ms: int
    stop_timeout_ms: int
    cooldown_ms: int
    healthcheck_path: str
    healthcheck_interval_ms: int


class LLMSidecarManager:
    def __init__(
        self,
        config: SidecarConfig,
        popen_factory: Optional[Callable[..., subprocess.Popen]] = None,
        health_checker: Optional[HealthCheckFn] = None,
    ) -> None:
        self._config = config
        self._popen_factory = popen_factory or subprocess.Popen
        self._health_checker = health_checker or self._default_health_check

        self._lock = threading.Lock()
        self._process: Optional[subprocess.Popen] = None
        self._active_port: Optional[int] = None
        self._cooldown_until = 0.0
        self._last_health_ok_at = 0.0
        self._last_error = ""

    @property
    def last_error(self) -> str:
        with self._lock:
            return self._last_error

    def chat_completions_endpoint(self) -> Optional[str]:
        with self._lock:
            if self._active_port is None:
                return None
            return self._chat_endpoint_for_port(self._active_port)

    def ensure_running(self) -> Optional[str]:
        if not self._config.enabled:
            return None
        if not self._config.binary_path:
            self._set_error("Sidecar enabled but binary path is empty")
            return None

        now = time.time()
        with self._lock:
            if now < self._cooldown_until:
                return None

            if self._is_process_running_locked():
                if self._is_healthy_cached_locked(now):
                    return self._chat_endpoint_for_port(self._active_port)

            if self._is_process_running_locked():
                if self._check_health_locked(timeout_s=0.2):
                    self._last_health_ok_at = now
                    return self._chat_endpoint_for_port(self._active_port)
                self._terminate_locked()

            start_port = self._resolve_port_locked()
            command = self._build_command(start_port)
            if not command:
                self._register_failure_locked("Sidecar command template produced empty command")
                return None

            try:
                self._process = self._popen_factory(
                    command,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except Exception as exc:
                self._register_failure_locked("Failed to start sidecar: {}".format(exc))
                return None

            self._active_port = start_port
            self._last_health_ok_at = 0.0

        if self._wait_until_healthy(start_port):
            with self._lock:
                self._last_error = ""
                self._last_health_ok_at = time.time()
            return self._chat_endpoint_for_port(start_port)

        with self._lock:
            self._register_failure_locked("Sidecar failed health check before startup timeout")
            self._terminate_locked()
        return None

    def stop(self) -> None:
        with self._lock:
            self._terminate_locked()

    def restart(self) -> Optional[str]:
        self.stop()
        return self.ensure_running()

    def _is_process_running_locked(self) -> bool:
        return self._process is not None and self._process.poll() is None and self._active_port is not None

    def _is_healthy_cached_locked(self, now: float) -> bool:
        interval_s = max(0.0, self._config.healthcheck_interval_ms / 1000.0)
        return self._last_health_ok_at > 0.0 and (now - self._last_health_ok_at) < interval_s

    def _check_health_locked(self, timeout_s: float) -> bool:
        if self._active_port is None:
            return False
        url = self._healthcheck_url_for_port(self._active_port)
        return self._health_checker(url, timeout_s)

    def _wait_until_healthy(self, port: int) -> bool:
        timeout_s = max(0.25, self._config.startup_timeout_ms / 1000.0)
        deadline = time.time() + timeout_s
        url = self._healthcheck_url_for_port(port)

        while time.time() < deadline:
            with self._lock:
                if self._process is None:
                    return False
                if self._process.poll() is not None:
                    return False

            if self._health_checker(url, 0.25):
                return True
            time.sleep(0.08)

        return False

    def _resolve_port_locked(self) -> int:
        if self._config.port > 0:
            return int(self._config.port)

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind((self._config.host, 0))
            return int(sock.getsockname()[1])
        finally:
            sock.close()

    def _build_command(self, port: int) -> List[str]:
        template = list(self._config.command_template)
        if not template:
            template = [
                "{binary_path}",
                "--model",
                "{model_path}",
                "--host",
                "{host}",
                "--port",
                "{port}",
            ]

        values = {
            "binary_path": self._config.binary_path,
            "model_path": self._config.model_path,
            "host": self._config.host,
            "port": str(port),
        }

        command: List[str] = []
        for token in template:
            if not isinstance(token, str) or not token:
                continue
            rendered = token.format(**values).strip()
            if rendered:
                command.append(rendered)
        return command

    def _register_failure_locked(self, error: str) -> None:
        self._last_error = error
        cooldown_s = max(0.0, self._config.cooldown_ms / 1000.0)
        self._cooldown_until = time.time() + cooldown_s

    def _set_error(self, error: str) -> None:
        with self._lock:
            self._last_error = error

    def _terminate_locked(self) -> None:
        process = self._process
        self._process = None
        self._active_port = None
        self._last_health_ok_at = 0.0

        if process is None:
            return

        if process.poll() is None:
            process.terminate()
            timeout_s = max(0.1, self._config.stop_timeout_ms / 1000.0)
            try:
                process.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1.0)

    def _chat_endpoint_for_port(self, port: int) -> str:
        return "http://{}:{}/v1/chat/completions".format(self._config.host, port)

    def _healthcheck_url_for_port(self, port: int) -> str:
        path = self._config.healthcheck_path or "/v1/models"
        if not path.startswith("/"):
            path = "/" + path
        return "http://{}:{}{}".format(self._config.host, port, path)

    def _default_health_check(self, url: str, timeout_s: float) -> bool:
        request = urllib.request.Request(url=url, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=timeout_s) as response:
                return 200 <= int(response.status) < 300
        except (urllib.error.URLError, ValueError, socket.timeout, TimeoutError):
            return False


def sidecar_config_from_settings(settings) -> SidecarConfig:
    command_template_raw = settings.get("llm_sidecar_command_template", [])
    command_template: Tuple[str, ...]
    if isinstance(command_template_raw, Sequence) and not isinstance(command_template_raw, (str, bytes)):
        command_template = tuple(str(item) for item in command_template_raw if str(item).strip())
    else:
        command_template = ()

    return SidecarConfig(
        enabled=bool(settings.get("llm_sidecar_auto_start", False)),
        binary_path=str(settings.get("llm_sidecar_binary_path", "") or "").strip(),
        model_path=str(settings.get("llm_sidecar_model_path", "") or "").strip(),
        host=str(settings.get("llm_sidecar_host", "127.0.0.1") or "127.0.0.1").strip(),
        port=int(settings.get("llm_sidecar_port", 0)),
        command_template=command_template,
        startup_timeout_ms=int(settings.get("llm_sidecar_startup_timeout_ms", 12000)),
        stop_timeout_ms=int(settings.get("llm_sidecar_stop_timeout_ms", 2000)),
        cooldown_ms=int(settings.get("llm_sidecar_cooldown_ms", 5000)),
        healthcheck_path=str(settings.get("llm_sidecar_healthcheck_path", "/v1/models") or "/v1/models").strip(),
        healthcheck_interval_ms=int(settings.get("llm_sidecar_healthcheck_interval_ms", 1500)),
    )
