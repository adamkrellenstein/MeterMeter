import hashlib
import os
import shutil
import stat
import tempfile
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Optional, Sequence


class BootstrapError(RuntimeError):
    pass


@dataclass
class BootstrapResult:
    runtime_path: str
    model_path: str
    changed: bool


def _expand(path: str) -> str:
    return os.path.abspath(os.path.expanduser(path.strip()))


def default_install_dir() -> str:
    return os.path.join(os.path.expanduser("~"), ".poetrymeter", "llm")


def _mkdir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def _filename_from_url(url: str, fallback: str) -> str:
    parsed = urllib.parse.urlparse(url)
    name = os.path.basename(parsed.path or "")
    name = name.strip()
    if not name:
        return fallback
    return name


def _validate_existing_file(path: str, label: str) -> str:
    resolved = _expand(path)
    if os.path.isfile(resolved):
        return resolved
    raise BootstrapError("{} path does not exist: {}".format(label, resolved))


def _find_runtime_binary(explicit_path: str, candidates: Sequence[str]) -> Optional[str]:
    if explicit_path.strip():
        path = _expand(explicit_path)
        if os.path.isfile(path):
            return path

    for name in candidates:
        found = shutil.which(name)
        if found:
            return found
    return None


def _download_file(
    url: str,
    dest_path: str,
    expected_sha256: str = "",
    timeout_s: float = 90.0,
    overwrite: bool = False,
) -> bool:
    if not url.strip():
        raise BootstrapError("Missing URL for download target: {}".format(dest_path))

    _mkdir(os.path.dirname(dest_path))
    if os.path.exists(dest_path) and not overwrite:
        return False

    expected = expected_sha256.strip().lower()

    fd, temp_path = tempfile.mkstemp(prefix="pm-bootstrap-", suffix=".part", dir=os.path.dirname(dest_path))
    os.close(fd)

    hasher = hashlib.sha256()
    req = urllib.request.Request(url=url, headers={"User-Agent": "PoetryMeter/1.0"})

    try:
        with urllib.request.urlopen(req, timeout=max(5.0, timeout_s)) as response:
            with open(temp_path, "wb") as out:
                while True:
                    chunk = response.read(1024 * 64)
                    if not chunk:
                        break
                    out.write(chunk)
                    hasher.update(chunk)

        if expected:
            actual = hasher.hexdigest().lower()
            if actual != expected:
                raise BootstrapError(
                    "SHA256 mismatch for {}. expected={}, actual={}".format(dest_path, expected, actual)
                )

        os.replace(temp_path, dest_path)
    except Exception as exc:
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except OSError:
            pass
        if isinstance(exc, BootstrapError):
            raise
        raise BootstrapError("Download failed for {}: {}".format(url, exc))

    return True


def _ensure_executable(path: str) -> None:
    mode = os.stat(path).st_mode
    os.chmod(path, mode | stat.S_IXUSR)


def bootstrap_local_llm(settings) -> BootstrapResult:
    install_dir = str(settings.get("llm_bootstrap_install_dir", default_install_dir()) or default_install_dir())
    install_dir = _expand(install_dir)
    _mkdir(install_dir)

    runtime_candidates = settings.get("llm_bootstrap_runtime_candidates", ["llama-server", "llamafile"])
    if not isinstance(runtime_candidates, list):
        runtime_candidates = ["llama-server", "llamafile"]

    runtime_path_setting = str(settings.get("llm_sidecar_binary_path", "") or "")
    model_path_setting = str(settings.get("llm_sidecar_model_path", "") or "")

    runtime_url = str(settings.get("llm_bootstrap_runtime_url", "") or "").strip()
    runtime_sha = str(settings.get("llm_bootstrap_runtime_sha256", "") or "").strip()
    model_url = str(settings.get("llm_bootstrap_model_url", "") or "").strip()
    model_sha = str(settings.get("llm_bootstrap_model_sha256", "") or "").strip()
    timeout_ms = int(settings.get("llm_bootstrap_download_timeout_ms", 120000))
    overwrite = bool(settings.get("llm_bootstrap_overwrite", False))

    changed = False

    runtime_path = None
    if runtime_path_setting.strip():
        runtime_path = _validate_existing_file(runtime_path_setting, "Runtime")
    else:
        runtime_path = _find_runtime_binary("", runtime_candidates)

    if runtime_path is None:
        runtime_name = str(settings.get("llm_bootstrap_runtime_filename", "llama-server") or "llama-server").strip()
        runtime_name = runtime_name or "llama-server"
        if runtime_url:
            runtime_name = _filename_from_url(runtime_url, runtime_name)

        runtime_path = os.path.join(install_dir, runtime_name)
        if os.path.isfile(runtime_path) and not overwrite:
            _ensure_executable(runtime_path)
        else:
            did_download = _download_file(
                url=runtime_url,
                dest_path=runtime_path,
                expected_sha256=runtime_sha,
                timeout_s=max(10.0, timeout_ms / 1000.0),
                overwrite=overwrite,
            )
            _ensure_executable(runtime_path)
            changed = changed or did_download

    model_path = None
    if model_path_setting.strip():
        model_path = _validate_existing_file(model_path_setting, "Model")

    if model_path is None:
        model_name = str(settings.get("llm_bootstrap_model_filename", "model.gguf") or "model.gguf").strip()
        model_name = model_name or "model.gguf"
        if model_url:
            model_name = _filename_from_url(model_url, model_name)

        model_path = os.path.join(install_dir, model_name)
        if not (os.path.isfile(model_path) and not overwrite):
            did_download = _download_file(
                url=model_url,
                dest_path=model_path,
                expected_sha256=model_sha,
                timeout_s=max(10.0, timeout_ms / 1000.0),
                overwrite=overwrite,
            )
            changed = changed or did_download

    if not os.path.isfile(runtime_path):
        raise BootstrapError("Runtime file missing after bootstrap: {}".format(runtime_path))
    if not os.path.isfile(model_path):
        raise BootstrapError("Model file missing after bootstrap: {}".format(model_path))

    return BootstrapResult(runtime_path=runtime_path, model_path=model_path, changed=changed)
