"""Sprzątanie zasobów uruchomionych przez bieżącą sesję laboratorium."""

from .guardian import cleanup_session_containers

__all__ = ["cleanup_session_containers"]
