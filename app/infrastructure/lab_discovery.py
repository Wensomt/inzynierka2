from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from app.config import ConfigStore


DockerClientFactory = Callable[[], Any]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class ExecResult:
    exit_code: int
    output: str


class DockerUnavailable(RuntimeError):
    pass


class DockerLabDiscovery:
    """Detect the simulator container and key binaries without hard-coded IDs."""

    DEFAULT_LABELS = {
        "oran-lab.role": "simulator",
        "oran-lab.engine": "mmwave-lena-flexric",
    }

    def __init__(
        self,
        config: ConfigStore,
        docker_client_factory: Optional[DockerClientFactory] = None,
    ) -> None:
        self.config = config
        self._docker_client_factory = docker_client_factory

    def status(self) -> Dict[str, Any]:
        return self.discover(save=False)

    def discover(
        self,
        save: bool = True,
        container: Optional[Any] = None,
        client: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Odczytuje inwentarz wskazanego kontenera lub kontenera z konfiguracji.

        Opcjonalny kontener pozwala aplikacji desktopowej inwentaryzować własną,
        oznakowaną sesję bez wyszukiwania cudzych zasobów Docker.
        """
        cfg = self.config.as_dict()
        lab_cfg = cfg.get("lab", {})
        warnings: List[str] = []
        errors: List[str] = []
        snapshot: Dict[str, Any] = {
            "schema_version": 1,
            "generated_at": utc_now(),
            "docker": {
                "available": False,
                "sdk_available": False,
                "server_version": None,
                "error": None,
            },
            "simulator": {
                "image_name": lab_cfg.get("simulator_image", "wcnc_tap"),
                "image_found": False,
                "container_found": False,
                "container": None,
            },
            "components": self._empty_components(),
            "scenarios": [],
            "xapps": self._configured_xapps(cfg),
            "capabilities": {
                "execution_backend": "docker",
                "docker_execution": False,
                "local_execution": False,
                "ric_control": "unverified",
                "kpm": "unverified",
            },
            "warnings": warnings,
            "errors": errors,
        }

        try:
            client = client or self._client()
            snapshot["docker"]["sdk_available"] = True
            ping = getattr(client, "ping", None)
            if callable(ping):
                ping()
            snapshot["docker"]["available"] = True
            snapshot["docker"]["server_version"] = self._server_version(client)
        except DockerUnavailable as exc:
            message = str(exc)
            snapshot["docker"]["error"] = message
            warnings.append(message)
            return snapshot
        except Exception as exc:
            message = "Docker daemon is not reachable: {}".format(exc)
            snapshot["docker"]["error"] = message
            errors.append(message)
            return snapshot

        image_name = str(snapshot["simulator"]["image_name"])
        image = self._find_image(client, image_name)
        snapshot["simulator"]["image_found"] = image is not None
        if image is not None:
            snapshot["simulator"]["image"] = self._image_info(image)

        container = container or self._find_container(client, lab_cfg, image_name)
        if container is None:
            warnings.append(
                "No simulator container found by label, configured name, or image {}.".format(
                    image_name
                )
            )
            return snapshot

        snapshot["simulator"]["container_found"] = True
        snapshot["simulator"]["container"] = self._container_info(container)
        snapshot["capabilities"]["docker_execution"] = (
            snapshot["simulator"]["container"].get("status") == "running"
        )

        if snapshot["simulator"]["container"].get("status") != "running":
            warnings.append("Simulator container is present but not running.")
            return snapshot

        inventory = self._container_inventory(container)
        self._apply_inventory(snapshot, inventory)
        if save:
            self.config.save_discovery_snapshot(snapshot)
        return snapshot

    def capabilities(self) -> Dict[str, Any]:
        snapshot = self.status()
        return {
            "docker": snapshot["docker"],
            "simulator": snapshot["simulator"],
            "capabilities": snapshot["capabilities"],
            "scenarios_count": len(snapshot["scenarios"]),
            "xapps_count": len(snapshot["xapps"]),
            "warnings": snapshot["warnings"],
            "errors": snapshot["errors"],
        }

    def _client(self) -> Any:
        if self._docker_client_factory is not None:
            return self._docker_client_factory()
        try:
            import docker  # type: ignore[import-not-found]
        except Exception as exc:
            raise DockerUnavailable(
                "Docker SDK for Python is not installed or cannot be imported: {}".format(exc)
            ) from exc
        return docker.from_env()

    @staticmethod
    def _server_version(client: Any) -> Optional[str]:
        try:
            version = client.version()
        except Exception:
            return None
        if isinstance(version, dict):
            return str(version.get("Version") or version.get("ApiVersion") or "")
        return str(version) if version else None

    def _find_image(self, client: Any, image_name: str) -> Any:
        images = getattr(client, "images", None)
        if images is None:
            return None
        get_image = getattr(images, "get", None)
        if callable(get_image):
            try:
                return get_image(image_name)
            except Exception:
                pass
        list_images = getattr(images, "list", None)
        if callable(list_images):
            try:
                for image in list_images(name=image_name):
                    if self._image_matches(image, image_name):
                        return image
            except TypeError:
                for image in list_images():
                    if self._image_matches(image, image_name):
                        return image
            except Exception:
                return None
        return None

    def _find_container(self, client: Any, lab_cfg: Dict[str, Any], image_name: str) -> Any:
        containers = getattr(client, "containers", None)
        if containers is None:
            return None
        list_containers = getattr(containers, "list", None)
        if not callable(list_containers):
            return None

        label_candidates = dict(lab_cfg.get("labels") or self.DEFAULT_LABELS)
        for key, value in label_candidates.items():
            try:
                found = list_containers(all=True, filters={"label": "{}={}".format(key, value)})
                if found:
                    return found[0]
            except Exception:
                pass

        try:
            all_containers = list_containers(all=True)
        except TypeError:
            all_containers = list_containers()

        configured_names = set(str(item) for item in lab_cfg.get("container_names", []))
        configured_id = str(lab_cfg.get("container_id", "") or "")
        for container in all_containers:
            info = self._container_info(container)
            if configured_id and str(info.get("id", "")).startswith(configured_id):
                return container
            if info.get("name") in configured_names:
                return container
        for container in all_containers:
            info = self._container_info(container)
            image_tags = info.get("image_tags") or []
            if any(self._image_name_equal(tag, image_name) for tag in image_tags):
                return container
        return None

    def _container_inventory(self, container: Any) -> Dict[str, Any]:
        result = self._exec_json(container, self._inventory_script())
        if result.exit_code != 0:
            return {"errors": ["Inventory command failed: {}".format(result.output)]}
        try:
            data = json.loads(result.output)
        except json.JSONDecodeError:
            return {"errors": ["Inventory command did not return JSON: {}".format(result.output[:500])]}
        return data if isinstance(data, dict) else {"errors": ["Inventory JSON is not an object."]}

    @staticmethod
    def _exec_json(container: Any, script: str) -> ExecResult:
        command = ["/bin/sh", "-lc", script]
        raw = container.exec_run(command, stdout=True, stderr=True)
        if isinstance(raw, tuple):
            exit_code, output = raw
        else:
            exit_code = getattr(raw, "exit_code", 1)
            output = getattr(raw, "output", b"")
        if isinstance(output, bytes):
            text = output.decode("utf-8", errors="replace")
        else:
            text = str(output)
        return ExecResult(int(exit_code or 0), text.strip())

    @staticmethod
    def _inventory_script() -> str:
        return r"""
python3 - <<'PY'
import glob
import json
import os
import platform
import shutil
import subprocess

def run(cmd, cwd=None):
    try:
        out = subprocess.check_output(cmd, cwd=cwd, stderr=subprocess.STDOUT, text=True, timeout=4)
        return out.strip()
    except Exception as exc:
        return ""

def first(paths):
    for item in paths:
        if item and os.path.exists(item):
            return item
    return None

def find_files(roots, names=None, suffixes=None, contains=None, limit=100, executable=None):
    found = []
    for root in roots:
        if not root or not os.path.exists(root):
            continue
        for current, dirs, files in os.walk(root):
            if len(found) >= limit:
                return found
            depth = current[len(root):].count(os.sep)
            if depth > 6:
                dirs[:] = []
                continue
            for name in files:
                path = os.path.join(current, name)
                if names and name not in names:
                    continue
                if suffixes and not any(name.endswith(s) for s in suffixes):
                    continue
                if contains and not any(token.lower() in name.lower() for token in contains):
                    continue
                if executable is True and not os.access(path, os.X_OK):
                    continue
                found.append(path)
                if len(found) >= limit:
                    return found
    return found

roots = ["/workspace", "/opt", "/usr/local", os.path.expanduser("~")]
near_rt_ric = first(find_files(roots, names={"nearRT-RIC"}, executable=True, limit=20))
ns3_wrapper = first(find_files(roots, names={"ns3"}, limit=50))
ns3_root = os.path.dirname(ns3_wrapper) if ns3_wrapper else None
flexric_root = first([p for p in ["/workspace/flexric", "/opt/flexric", "/usr/local/flexric"] if os.path.isdir(p)])
e2sim_root = first([p for p in ["/workspace/e2sim", "/workspace/e2sim-kpmv3", "/opt/e2sim"] if os.path.isdir(p)])

scenario_roots = []
if ns3_root:
    scenario_roots.extend([os.path.join(ns3_root, "scratch"), os.path.join(ns3_root, "examples")])
scenarios = []
for path in find_files(scenario_roots, suffixes={".cc", ".py"}, limit=200):
    scenarios.append({"id": os.path.splitext(os.path.basename(path))[0], "name": os.path.basename(path), "source_path": path})

xapps = []
for path in find_files([flexric_root, "/workspace"], contains=["xapp", "xApp"], limit=200, executable=True):
    base = os.path.basename(path)
    if base in {"xapp", "xapp_api"}:
        continue
    xapps.append({"id": base, "name": base, "executable_path": path})

libs = find_files([flexric_root, "/usr/local/lib", "/usr/lib"], suffixes={".so", ".a"}, contains=["kpm", "e2ap", "rc", "flexric"], limit=100)
configs = find_files([flexric_root, "/usr/local/etc", "/etc"], suffixes={".conf", ".json", ".yaml", ".yml"}, contains=["flexric", "ric", "e2"], limit=100)

git_dirs = {
    "flexric": flexric_root,
    "ns3": ns3_root,
    "e2sim": e2sim_root,
}
commits = {name: run(["git", "-C", path, "rev-parse", "HEAD"]) for name, path in git_dirs.items() if path}

os_release = ""
try:
    with open("/etc/os-release", "r", encoding="utf-8", errors="replace") as handle:
        os_release = handle.read()
except OSError:
    pass

print(json.dumps({
    "near_rt_ric": near_rt_ric,
    "ns3_wrapper": ns3_wrapper,
    "ns3_root": ns3_root,
    "flexric_root": flexric_root,
    "e2sim_root": e2sim_root,
    "scenarios": scenarios,
    "xapps": xapps,
    "libraries": libs,
    "flexric_configs": configs,
    "versions": {
        "gcc": run(["gcc", "--version"]).splitlines()[0] if run(["gcc", "--version"]) else None,
        "python": platform.python_version(),
        "os": os_release,
        "uname": run(["uname", "-a"]),
    },
    "commits": commits,
}, ensure_ascii=False))
PY
"""

    def _apply_inventory(self, snapshot: Dict[str, Any], inventory: Dict[str, Any]) -> None:
        for error in inventory.get("errors", []):
            snapshot["errors"].append(str(error))

        components = snapshot["components"]
        self._component_path(components, "near_rt_ric", inventory.get("near_rt_ric"))
        self._component_path(components, "ns3_root", inventory.get("ns3_root"))
        self._component_path(components, "ns3_wrapper", inventory.get("ns3_wrapper"))
        self._component_path(components, "flexric_root", inventory.get("flexric_root"))
        self._component_path(components, "e2sim_root", inventory.get("e2sim_root"))

        versions = inventory.get("versions") or {}
        components["gcc"].update({
            "detected": bool(versions.get("gcc")),
            "status": "detected" if versions.get("gcc") else "missing",
            "version": versions.get("gcc"),
        })
        components["container_os"].update({
            "detected": bool(versions.get("os") or versions.get("uname")),
            "status": "detected" if versions.get("os") or versions.get("uname") else "missing",
            "details": {"os_release": versions.get("os"), "uname": versions.get("uname")},
        })
        components["flexric_libraries"].update({
            "detected": bool(inventory.get("libraries")),
            "status": "detected" if inventory.get("libraries") else "missing",
            "details": {"paths": inventory.get("libraries", [])},
        })
        components["flexric_configs"].update({
            "detected": bool(inventory.get("flexric_configs")),
            "status": "detected" if inventory.get("flexric_configs") else "missing",
            "details": {"paths": inventory.get("flexric_configs", [])},
        })
        components["commits"].update({
            "detected": bool(inventory.get("commits")),
            "status": "detected" if inventory.get("commits") else "missing",
            "details": inventory.get("commits", {}),
        })

        snapshot["scenarios"] = self._dedupe_records(inventory.get("scenarios", []), "source_path")
        detected_xapps = self._dedupe_records(inventory.get("xapps", []), "executable_path")
        snapshot["xapps"] = self._merge_xapps(snapshot["xapps"], detected_xapps)
        snapshot["capabilities"]["kpm"] = (
            "detected" if any("kpm" in str(path).lower() for path in inventory.get("libraries", []))
            else "unverified"
        )

    @staticmethod
    def _component_path(components: Dict[str, Dict[str, Any]], key: str, path: Optional[str]) -> None:
        components[key].update({
            "detected": bool(path),
            "status": "detected" if path else "missing",
            "path": path,
        })

    @staticmethod
    def _empty_components() -> Dict[str, Dict[str, Any]]:
        labels = {
            "near_rt_ric": "nearRT-RIC executable",
            "ns3_root": "ns-3 root",
            "ns3_wrapper": "ns3 wrapper",
            "flexric_root": "FlexRIC root",
            "e2sim_root": "E2Sim root",
            "gcc": "GCC",
            "container_os": "Container OS",
            "flexric_libraries": "FlexRIC libraries",
            "flexric_configs": "FlexRIC config files",
            "commits": "Git commits",
        }
        return {
            key: {
                "key": key,
                "label": label,
                "detected": False,
                "status": "unknown",
                "path": None,
                "version": None,
                "details": {},
                "error": None,
            }
            for key, label in labels.items()
        }

    @staticmethod
    def _configured_xapps(cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
        result = []
        for key, item in (cfg.get("xapps") or {}).items():
            result.append({
                "id": key,
                "name": item.get("label") or key,
                "executable_path": item.get("executable") or "",
                "configured": True,
                "capabilities": item.get("capabilities", {}),
            })
        return result

    @staticmethod
    def _merge_xapps(configured: List[Dict[str, Any]], detected: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        result = list(configured)
        seen = {str(item.get("executable_path")) for item in result if item.get("executable_path")}
        for item in detected:
            path = str(item.get("executable_path") or "")
            if path and path not in seen:
                item = dict(item)
                item["configured"] = False
                result.append(item)
                seen.add(path)
        return result

    @staticmethod
    def _dedupe_records(records: Iterable[Dict[str, Any]], key: str) -> List[Dict[str, Any]]:
        result = []
        seen = set()
        for record in records:
            marker = str(record.get(key) or record)
            if marker in seen:
                continue
            seen.add(marker)
            result.append(dict(record))
        return result

    @staticmethod
    def _image_info(image: Any) -> Dict[str, Any]:
        attrs = getattr(image, "attrs", {}) or {}
        return {
            "id": getattr(image, "id", attrs.get("Id", "")),
            "tags": list(getattr(image, "tags", attrs.get("RepoTags", []) or [])),
            "digest": attrs.get("RepoDigests", []),
        }

    @staticmethod
    def _container_info(container: Any) -> Dict[str, Any]:
        attrs = getattr(container, "attrs", {}) or {}
        config = attrs.get("Config", {}) if isinstance(attrs, dict) else {}
        state = attrs.get("State", {}) if isinstance(attrs, dict) else {}
        image = getattr(container, "image", None)
        image_tags = list(getattr(image, "tags", []) or [])
        if not image_tags and config.get("Image"):
            image_tags = [config["Image"]]
        labels = getattr(container, "labels", None) or config.get("Labels") or {}
        return {
            "id": getattr(container, "id", attrs.get("Id", "")),
            "short_id": getattr(container, "short_id", "") or str(getattr(container, "id", ""))[:12],
            "name": getattr(container, "name", attrs.get("Name", "")).lstrip("/"),
            "status": getattr(container, "status", None) or state.get("Status"),
            "image_tags": image_tags,
            "labels": labels,
        }

    @staticmethod
    def _image_matches(image: Any, image_name: str) -> bool:
        return any(
            DockerLabDiscovery._image_name_equal(tag, image_name)
            for tag in getattr(image, "tags", []) or []
        )

    @staticmethod
    def _image_name_equal(tag: str, image_name: str) -> bool:
        normalized = tag.split("@", 1)[0]
        candidates = {normalized, normalized.split(":", 1)[0]}
        if "/" in normalized:
            candidates.add(normalized.rsplit("/", 1)[-1])
            candidates.add(normalized.rsplit("/", 1)[-1].split(":", 1)[0])
        target = image_name.split("@", 1)[0]
        return target in candidates or "{}:latest".format(target) in candidates
