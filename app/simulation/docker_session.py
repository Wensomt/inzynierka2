from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional
from uuid import uuid4

from app.cleanup.guardian import (
    MANAGED_LABEL,
    OWNER_PID_LABEL,
    SESSION_LABEL,
    cleanup_session_containers,
    process_is_alive,
)


class DockerSessionError(RuntimeError):
    """Błąd sesji z opcjonalnym identyfikatorem do bezpiecznego recovery."""

    def __init__(self, message: str, session_id: Optional[str] = None) -> None:
        super().__init__(message)
        self.session_id = str(session_id or "")


@dataclass
class DockerSession:
    id: str
    client: Any
    container: Any
    guardian: Optional[subprocess.Popen] = None


class DockerSessionManager:
    """Tworzy i usuwa kontener należący tylko do jednej symulacji."""

    def __init__(self, lab_config: Mapping[str, Any]) -> None:
        self.lab_config = dict(lab_config)

    def preflight(self) -> Dict[str, Any]:
        checks: List[str] = []
        errors: List[str] = []
        client = None
        try:
            client = self._client()
            client.ping()
            checks.append("Docker dostępny")
            image_name = self.image_name
            try:
                client.images.get(image_name)
                checks.append("Obraz Docker dostępny: {}".format(image_name))
            except Exception as exc:
                errors.append("Nie można odczytać obrazu Docker {}: {}".format(image_name, exc))
        except Exception as exc:
            return {
                "ok": False,
                "checks": checks,
                "errors": ["Docker nie jest dostępny: {}".format(exc)],
            }
        finally:
            self._close_client(client)
        return {"ok": not errors, "checks": checks, "errors": errors}

    @property
    def image_name(self) -> str:
        return str(self.lab_config.get("simulator_image") or "wcnc_tap")

    def start(self) -> DockerSession:
        preflight = self.preflight()
        if not preflight["ok"]:
            raise DockerSessionError("; ".join(preflight["errors"]))
        client = self._client()
        session_id = uuid4().hex
        labels = {
            MANAGED_LABEL: "true",
            SESSION_LABEL: session_id,
            OWNER_PID_LABEL: str(os.getpid()),
        }
        name = "oran-energy-lab-{}".format(session_id[:12])
        try:
            container = client.containers.run(
                self.image_name,
                command=["/bin/bash", "-lc", "exec sleep infinity"],
                name=name,
                detach=True,
                labels=labels,
                init=True,
                tty=True,
                stdin_open=True,
                remove=False,
            )
            container.reload()
            if getattr(container, "status", "") != "running":
                raise DockerSessionError("Kontener symulatora nie uruchomił się poprawnie")
            session = DockerSession(session_id, client, container)
            session.guardian = self._start_guardian(session_id)
            return session
        except Exception as exc:
            # Po częściowo udanym ``containers.run`` kontener może już mieć
            # naszą etykietę, mimo że ``start`` zwrócił błąd.  Zanim caller
            # dostanie wyjątek, próbujemy go usunąć i przekazujemy session_id
            # do ewentualnego ponownego potwierdzenia przed retry.
            cleanup_detail = ""
            try:
                self.ensure_session_cleanup(session_id, client)
            except Exception as cleanup_exc:
                cleanup_detail = "; cleanup sesji niepotwierdzony: {}".format(cleanup_exc)
            message = "Nie można uruchomić kontenera symulatora: {}{}".format(
                exc, cleanup_detail
            )
            self._close_client(client)
            raise DockerSessionError(message, session_id) from exc

    def stop(self, session: DockerSession) -> List[str]:
        # Każdy przebieg ma własny kontener.  Potwierdzenie jego usunięcia
        # jest potrzebne nie tylko przy normalnym końcu, ale też zanim
        # kontroler spróbuje ponowić nieudany run w świeżej sesji.
        try:
            return self.ensure_session_cleanup(session.id, session.client)
        finally:
            # Wcześniej błąd weryfikacji cleanupu zostawiał proces ochronny
            # aktywny aż do zamknięcia aplikacji.  Nie ma on już wtedy czego
            # chronić, a jego zatrzymanie nie może maskować pierwotnego błędu.
            self._stop_guardian(session.guardian)
            self._close_client(session.client)

    def ensure_session_cleanup(
        self,
        session_id: str,
        client: Optional[Any] = None,
    ) -> List[str]:
        """Wymusza i potwierdza cleanup dokładnie jednej sesji.

        Metoda nie dotyka innych kontenerów Dockera.  Jest używana po
        nieudanym przebiegu jako ostatnia kontrola przed automatycznym retry;
        jeśli nie można potwierdzić pustej listy kontenerów oznaczonych tym
        samym ``session_id``, caller nie powinien uruchamiać kolejnej próby.
        """

        if not session_id:
            return []
        active_client = client or self._client()
        try:
            removed = cleanup_session_containers(session_id, active_client)
            remaining = self._session_container_names(session_id, active_client)
            if remaining:
                raise DockerSessionError(
                    "Nie udało się usunąć kontenera sesji: {}".format(", ".join(remaining))
                )
            return removed
        finally:
            if client is None:
                self._close_client(active_client)

    @staticmethod
    def _close_client(client: Optional[Any]) -> None:
        close = getattr(client, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                # Closing a transport must not mask the original failure.
                pass

    @staticmethod
    def _stop_guardian(guardian: Optional[subprocess.Popen]) -> None:
        if guardian is None or guardian.poll() is not None:
            return
        try:
            guardian.terminate()
            guardian.wait(timeout=2)
        except subprocess.TimeoutExpired:
            try:
                guardian.kill()
                guardian.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                pass
        except OSError:
            # It may have exited between poll() and terminate().
            pass

    def cleanup_orphans(self) -> List[str]:
        """Usuwa tylko zasoby naszej aplikacji, których właściciel już nie działa."""

        try:
            client = self._client()
            client.ping()
            containers = client.containers.list(
                all=True,
                filters={"label": "{}=true".format(MANAGED_LABEL)},
            )
        except Exception:
            return []
        cleaned: List[str] = []
        for container in containers:
            labels = getattr(container, "labels", {}) or {}
            session_id = str(labels.get(SESSION_LABEL) or "")
            try:
                owner_pid = int(labels.get(OWNER_PID_LABEL) or 0)
            except (TypeError, ValueError):
                continue
            if session_id and process_is_alive(owner_pid) is False:
                cleaned.extend(cleanup_session_containers(session_id, client))
        return cleaned

    @staticmethod
    def _session_container_names(session_id: str, client: Any) -> List[str]:
        filters = {
            "label": [
                "{}=true".format(MANAGED_LABEL),
                "{}={}".format(SESSION_LABEL, session_id),
            ]
        }
        try:
            containers = client.containers.list(all=True, filters=filters)
        except Exception as exc:
            raise DockerSessionError("Nie można potwierdzić cleanupu kontenera: {}".format(exc)) from exc
        return [
            str(getattr(container, "name", "") or getattr(container, "id", ""))
            for container in containers
            if (getattr(container, "labels", {}) or {}).get(MANAGED_LABEL) == "true"
            and (getattr(container, "labels", {}) or {}).get(SESSION_LABEL) == session_id
        ]

    @staticmethod
    def _client() -> Any:
        try:
            import docker  # type: ignore[import-not-found]
        except Exception as exc:
            raise DockerSessionError("Brak biblioteki Docker dla Pythona: {}".format(exc)) from exc
        return docker.from_env()

    @staticmethod
    def _start_guardian(session_id: str) -> subprocess.Popen:
        if getattr(sys, "frozen", False):
            command = [
                sys.executable,
                "--cleanup-guardian",
                "--parent-pid",
                str(os.getpid()),
                "--session",
                session_id,
            ]
        else:
            command = [
                sys.executable,
                "-m",
                "app.main",
                "--cleanup-guardian",
                "--parent-pid",
                str(os.getpid()),
                "--session",
                session_id,
            ]
        options: Dict[str, Any] = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        if os.name == "nt":
            options["creationflags"] = (
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                | getattr(subprocess, "DETACHED_PROCESS", 0)
            )
        else:
            options["start_new_session"] = True
        try:
            return subprocess.Popen(command, **options)
        except OSError as exc:
            raise DockerSessionError("Nie można uruchomić procesu ochronnego: {}".format(exc)) from exc
