from __future__ import annotations

import io
import json
import os
import re
import shutil
import tarfile
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple
from uuid import uuid4

from app.config import ConfigStore, RESOURCE_DIR
from app.services.my_energy_sources import source_path
from app.infrastructure.lab_discovery import DockerLabDiscovery
from app.simulation.docker_session import DockerSessionManager


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_slug(name: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9_]+", "_", str(name or "").strip()).strip("_").lower()
    if not text or not re.match(r"[a-zA-Z_]", text):
        raise ValueError("Nazwa xAppa musi zaczynać się od litery i zawierać litery, cyfry lub _.")
    if len(text) > 48:
        raise ValueError("Nazwa xAppa jest zbyt długa (maks. 48 znaków po uproszczeniu).")
    return text


@dataclass
class XAppRecord:
    key: str
    name: str
    executable: str
    source_path: Optional[Path]
    parameters: Dict[str, Any] = field(default_factory=dict)
    description: str = ""
    custom: bool = False
    target: Optional[str] = None
    created_at: Optional[str] = None
    last_build: Optional[str] = None
    last_build_status: Optional[str] = None
    last_build_log: Optional[Path] = None
    image: Optional[str] = None

    @property
    def buildable(self) -> bool:
        return bool(self.source_path and self.source_path.is_file() and self.target)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": 1,
            "key": self.key,
            "name": self.name,
            "description": self.description,
            "source_path": str(self.source_path) if self.source_path else None,
            "target": self.target,
            "parameters": self.parameters,
            "created_at": self.created_at,
            "last_build": self.last_build,
            "last_build_status": self.last_build_status,
            "last_build_log": str(self.last_build_log) if self.last_build_log else None,
            "image": self.image,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "XAppRecord":
        source_value = value.get("source_path")
        source = Path(str(source_value)) if source_value else None
        log_value = value.get("last_build_log")
        log = Path(str(log_value)) if log_value else None
        target = str(value.get("target") or "") or None
        return cls(
            key=str(value.get("key") or ""),
            name=str(value.get("name") or "xApp"),
            executable="./{}".format(target) if target else "",
            source_path=source,
            parameters=dict(value.get("parameters") or {}),
            description=str(value.get("description") or ""),
            custom=True,
            target=target,
            created_at=str(value.get("created_at") or "") or None,
            last_build=str(value.get("last_build") or "") or None,
            last_build_status=str(value.get("last_build_status") or "") or None,
            last_build_log=log,
            image=str(value.get("image") or "") or None,
        )


@dataclass
class XAppBuildResult:
    ok: bool
    message: str
    log_path: Path
    image: Optional[str] = None
    executable_cwd: Optional[str] = None


class XAppService:
    """Obsługa rzeczywistych źródeł xAppów oraz budowania do nowego obrazu.

    Nie modyfikuje obrazu bazowego. Zbudowany target trafia do tymczasowego
    kontenera uruchomionego z aktualnego obrazu, po sukcesie kontener jest
    commitowany jako nowy lokalny image, a konfiguracja wskazuje właśnie ten
    nowy tag. Błąd przed commitem nie zmienia konfiguracji ani obrazu bazowego.
    """

    BUILT_IMAGE_REPOSITORY = "oran-energy-lab-xapp"

    def __init__(self, root: Path, config: ConfigStore) -> None:
        self.root = Path(root)
        self.config = config
        self.projects_root = self.root / "projects"
        self.logs_root = self.root / "builds"
        self.projects_root.mkdir(parents=True, exist_ok=True)
        self.logs_root.mkdir(parents=True, exist_ok=True)

    def list(self) -> List[XAppRecord]:
        cfg = self.config.as_dict()
        configured = self._configured_records(cfg)
        custom = self._custom_records()
        # A completed custom build deliberately adds its runtime entry to the
        # main configuration.  The project manifest remains the authority for
        # source path and build history, though; otherwise the custom entry
        # would turn into a generic configured xApp after its first build.
        custom_keys = {item.key for item in custom}
        records = [item for item in configured if item.key not in custom_keys]
        records.extend(custom)
        return sorted(records, key=lambda item: (not item.custom, item.name.casefold(), item.key))

    def get(self, key: str) -> XAppRecord:
        for item in self.list():
            if item.key == key:
                return item
        raise FileNotFoundError("Nie znaleziono xAppa: {}".format(key))

    def create_from_my_energy_template(self, name: str) -> XAppRecord:
        slug = _safe_slug(name)
        unique_suffix = uuid4().hex[:8]
        key = "custom_{}_{}".format(slug, unique_suffix)
        # The source file and CMake target live in an image that can contain
        # several custom xApps.  A unique project key alone is not enough:
        # two xApps created from the same display name would otherwise replace
        # each other's target during a later build.
        target = "xapp_{}_{}".format(slug, unique_suffix)
        project = self.projects_root / key
        project.mkdir(parents=False, exist_ok=False)
        template = source_path(self.config.as_dict(), "xapp")
        if not template.is_file():
            raise FileNotFoundError("Brak źródła szablonu My Energy: {}".format(template))
        source = project / (target + ".c")
        shutil.copyfile(str(template), str(source))
        optimized = ((self.config.as_dict().get("xapps") or {}).get("optimized") or {})
        item = XAppRecord(
            key=key,
            name=" ".join(str(name).strip().split()),
            executable="./{}".format(target),
            source_path=source,
            parameters=dict(optimized.get("parameters") or {}),
            description="Kopia rzeczywistego xAppa My Energy/KPM z tego projektu.",
            custom=True,
            target=target,
            created_at=utc_now(),
        )
        if not item.name:
            raise ValueError("Podaj nazwę xAppa")
        self._write_manifest(item)
        return item

    def validate(self, record: XAppRecord) -> List[str]:
        errors: List[str] = []
        if not record.key:
            errors.append("Brak klucza xAppa.")
        if record.source_path is not None and not record.source_path.is_file():
            errors.append("Nie znaleziono źródła: {}".format(record.source_path))
        if record.buildable and not re.fullmatch(r"xapp_[A-Za-z0-9_]+", str(record.target)):
            errors.append("Niedozwolona nazwa targetu builda.")
        if record.custom and not record.buildable:
            errors.append("Własny xApp nie ma kompletnego źródła lub targetu.")
        return errors

    def build(self, key: str) -> XAppBuildResult:
        record = self.get(key)
        validation = self.validate(record)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        log_path = self.logs_root / "{}-{}.log".format(record.key, stamp)
        if validation:
            log_path.write_text("\n".join(validation) + "\n", encoding="utf-8")
            return self._finish_build(record, False, "; ".join(validation), log_path)
        if not record.buildable:
            message = "Brak lokalnego źródła buildowalnego dla tego xAppa."
            log_path.write_text(message + "\n", encoding="utf-8")
            return self._finish_build(record, False, message, log_path)

        session_manager = DockerSessionManager(self.config.as_dict().get("lab", {}))
        session = None
        chunks: List[str] = []
        try:
            session = session_manager.start()
            discovery = DockerLabDiscovery(self.config).discover(
                save=False, container=session.container, client=session.client
            )
            flexric_root = str(((discovery.get("components") or {}).get("flexric_root") or {}).get("path") or "")
            if not flexric_root:
                raise RuntimeError("Nie wykryto FlexRIC w obrazie Docker.")
            source_dir = self._find_source_dir(session.container, flexric_root, record.target or "")
            if not source_dir:
                raise RuntimeError("Nie wykryto katalogu CMake xAppa w FlexRIC.")
            remote_source = self._copy_source_to_container(session.container, record.source_path, record.target or "")
            target_source = "{}/{}.c".format(source_dir, record.target)
            code, output = self._exec(
                session.container,
                "cp {} {}".format(self._quote(remote_source), self._quote(target_source)),
            )
            chunks.append("$ cp source\n{}\n".format(output))
            if code != 0:
                raise RuntimeError("Nie można skopiować źródła xAppa do FlexRIC.")
            cmake_path = "{}/CMakeLists.txt".format(source_dir)
            code, output = self._exec(
                session.container,
                self._cmake_target_script(cmake_path, record.target or ""),
            )
            chunks.append("$ update CMakeLists.txt\n{}\n".format(output))
            if code != 0:
                raise RuntimeError("Nie można przygotować targetu CMake xAppa.")
            build_root = "{}/build".format(flexric_root)
            command = "cmake --build {} --target {} -j$(nproc)".format(
                self._quote(build_root), self._quote(record.target or "")
            )
            code, output = self._exec(session.container, command)
            chunks.append("$ {}\n{}\n".format(command, output))
            if code != 0:
                raise RuntimeError("Build xAppa zakończył się błędem (kod {}).".format(code))
            executable_cwd = self._find_built_executable(session.container, build_root, record.target or "")
            if not executable_cwd:
                raise RuntimeError("Build zakończył się bez wykrywalnej binarki xAppa.")
            image_tag = "{}:{}-{}".format(self.BUILT_IMAGE_REPOSITORY, record.key, stamp)
            repository, tag = image_tag.split(":", 1)
            session.container.commit(repository=repository, tag=tag)
            chunks.append("Zapisano nowy lokalny obraz: {}\n".format(image_tag))
            log_path.write_text("\n".join(chunks), encoding="utf-8")
            self._activate_built_xapp(record, image_tag, executable_cwd, log_path)
            return self._finish_build(
                record,
                True,
                "Zbudowano xApp i zapisano obraz {}.".format(image_tag),
                log_path,
                image_tag,
                executable_cwd,
            )
        except Exception as exc:
            chunks.append("BŁĄD: {}\n".format(exc))
            log_path.write_text("\n".join(chunks), encoding="utf-8")
            return self._finish_build(record, False, str(exc), log_path)
        finally:
            if session is not None:
                try:
                    session_manager.stop(session)
                except Exception as exc:
                    with log_path.open("a", encoding="utf-8") as handle:
                        handle.write("Cleanup kontenera builda nieudany: {}\n".format(exc))

    def _activate_built_xapp(
        self,
        record: XAppRecord,
        image_tag: str,
        executable_cwd: str,
        log_path: Path,
    ) -> None:
        data = self.config.as_dict()
        lab = data.setdefault("lab", {})
        lab["simulator_image"] = image_tag
        xapps = data.setdefault("xapps", {})
        entry = dict(xapps.get(record.key) or {})
        entry.update(
            {
                "label": record.name,
                "executable": "./{}".format(record.target),
                "cwd": executable_cwd,
                "parameters": dict(record.parameters),
                "last_build": utc_now(),
                "build_image": image_tag,
                "build_log": str(log_path),
            }
        )
        xapps[record.key] = entry
        self.config.save(data)

    def _finish_build(
        self,
        record: XAppRecord,
        ok: bool,
        message: str,
        log_path: Path,
        image: Optional[str] = None,
        executable_cwd: Optional[str] = None,
    ) -> XAppBuildResult:
        if record.custom:
            record.last_build = utc_now()
            record.last_build_status = "SUCCESS" if ok else "FAILED"
            record.last_build_log = log_path
            if image:
                record.image = image
            self._write_manifest(record)
        return XAppBuildResult(ok, message, log_path, image, executable_cwd)

    def _configured_records(self, cfg: Mapping[str, Any]) -> List[XAppRecord]:
        result: List[XAppRecord] = []
        source_map = {
            "optimized": source_path(cfg, "xapp"),
        }
        for key, raw in (cfg.get("xapps") or {}).items():
            if key == "none":
                continue
            item = dict(raw or {})
            executable = str(item.get("executable") or "")
            target = executable.replace("./", "").split("/")[-1] or None
            source = source_map.get(str(key))
            result.append(
                XAppRecord(
                    key=str(key),
                    name=str(item.get("label") or key),
                    executable=executable,
                    source_path=source if source and source.is_file() else None,
                    parameters=dict(item.get("parameters") or {}),
                    description="xApp skonfigurowany w config.json.",
                    custom=False,
                    target=target,
                    last_build=str(item.get("last_build") or "") or None,
                    last_build_log=Path(str(item.get("build_log"))) if item.get("build_log") else None,
                    image=str(item.get("build_image") or "") or None,
                )
            )
        return result

    def _custom_records(self) -> List[XAppRecord]:
        result = []
        for path in self.projects_root.glob("*/xapp.json"):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                item = XAppRecord.from_dict(value)
                if item.source_path is not None and not item.source_path.is_absolute():
                    item.source_path = path.parent / item.source_path
                if item.key:
                    result.append(item)
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                continue
        return result

    def _write_manifest(self, record: XAppRecord) -> None:
        if not record.custom:
            return
        project = self.projects_root / record.key
        project.mkdir(parents=True, exist_ok=True)
        payload = record.to_dict()
        if record.source_path is not None:
            try:
                payload["source_path"] = str(record.source_path.resolve().relative_to(project.resolve()))
            except ValueError:
                # An externally supplied source remains usable locally, while
                # generated projects retain their portable relative path.
                pass
        self._write_json(project / "xapp.json", payload)

    @staticmethod
    def _write_json(path: Path, value: Mapping[str, Any]) -> None:
        handle, temporary_name = tempfile.mkstemp(
            prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
        )
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(value, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            Path(temporary_name).replace(path)
        finally:
            Path(temporary_name).unlink(missing_ok=True)

    @staticmethod
    def _quote(value: str) -> str:
        # Posix quote without adding a shell dependency to this service API.
        return "'{}'".format(str(value).replace("'", "'\"'\"'"))

    @staticmethod
    def _exec(container: Any, command: str) -> Tuple[int, str]:
        raw = container.exec_run(["/bin/bash", "-lc", command], stdout=True, stderr=True)
        if isinstance(raw, tuple):
            code, output = raw
        else:
            code = getattr(raw, "exit_code", 1)
            output = getattr(raw, "output", b"")
        text = output.decode("utf-8", errors="replace") if isinstance(output, bytes) else str(output)
        return int(code if code is not None else 1), text

    @classmethod
    def _find_source_dir(self, container: Any, flexric_root: str, target: str) -> Optional[str]:
        """Select the actual CMake owner of *target* before using a fallback.

        FlexRIC images can contain both ``myxapp`` and ``orange`` examples.
        The project's supplied xApp is intentionally installed in whichever
        directory already declares its target, so blindly preferring one of
        them may create a duplicate CMake target.
        """
        candidates = [
            "{}/examples/xApp/c/myxapp".format(flexric_root),
            "{}/examples/xApp/c/orange".format(flexric_root),
        ]
        available: List[str] = []
        owners: List[str] = []
        # The existing installer uses this exact, line-local declaration too.
        # It also covers the normal CMake form where the target is the final
        # token on an ``add_executable(...)`` line and its sources follow on
        # subsequent lines.
        declaration = "add_executable({}".format(target)
        for path in candidates:
            cmake_path = "{}/CMakeLists.txt".format(path)
            code, _ = self._exec(container, "test -f {}".format(self._quote(cmake_path)))
            if code != 0:
                continue
            available.append(path)
            code, _ = self._exec(
                container,
                "grep -Fq -- {} {}".format(self._quote(declaration), self._quote(cmake_path)),
            )
            if code == 0:
                owners.append(path)
        if len(owners) > 1:
            raise RuntimeError(
                "Target xAppa {} występuje w kilku katalogach CMake: {}. "
                "Usuń duplikat przed buildem.".format(target, ", ".join(owners))
            )
        if owners:
            return owners[0]
        return available[0] if available else None

    def _copy_source_to_container(self, container: Any, source: Path, target: str) -> str:
        if not source.is_file():
            raise FileNotFoundError("Nie znaleziono źródła xAppa: {}".format(source))
        destination = "/tmp/oran-energy-lab-xapp-{}".format(uuid4().hex)
        code, output = self._exec(container, "mkdir -p {}".format(self._quote(destination)))
        if code != 0:
            raise RuntimeError("Nie można przygotować katalogu roboczego: {}".format(output))
        payload = io.BytesIO()
        with tarfile.open(fileobj=payload, mode="w") as archive:
            data = source.read_bytes()
            info = tarfile.TarInfo(name="{}.c".format(target))
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
        container.put_archive(destination, payload.getvalue())
        return "{}/{}.c".format(destination, target)

    @classmethod
    def _find_built_executable(self, container: Any, build_root: str, target: str) -> Optional[str]:
        command = "found=$(find {} -type f -name {} -perm -111 -print -quit); test -n \"$found\" && dirname \"$found\"".format(
            self._quote(build_root), self._quote(target)
        )
        code, output = self._exec(container, command)
        return output.strip() if code == 0 and output.strip() else None

    @classmethod
    def _cmake_target_script(self, cmake_path: str, target: str) -> str:
        block = "\n".join(
            [
                "add_executable({}".format(target),
                "    {}.c".format(target),
                "    ${CMAKE_SOURCE_DIR}/src/util/alg_ds/alg/defer.c",
                ")",
                "",
                "target_link_libraries({}".format(target),
                "    PUBLIC",
                "    e42_xapp",
                "    pthread",
                "    sctp",
                "    dl",
                ")",
                "",
            ]
        )
        encoded = json.dumps(block)
        python = (
            "from pathlib import Path; import sys; path=Path(sys.argv[1]); target=sys.argv[2]; block=sys.argv[3]; "
            "text=path.read_text(encoding='utf-8'); "
            "path.write_text(text if ('add_executable(' + target) in text else text.rstrip() + '\\n\\n' + block, encoding='utf-8')"
        )
        return "python3 -c {} {} {} {}".format(
            self._quote(python), self._quote(cmake_path), self._quote(target), self._quote(block)
        )
