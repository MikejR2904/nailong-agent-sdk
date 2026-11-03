"""Saved API keys, kept in the operating system's credential store and never in a settings file."""

from __future__ import annotations

from typing import Any

SERVICE = "nailong-cli"


class CredentialError(ValueError):
    pass


def _keyring() -> Any | None:
    try:
        import keyring
    except ImportError:
        return None
    return keyring


def available() -> bool:
    return _keyring() is not None


def backend_name() -> str:
    module = _keyring()
    if module is None:
        return "none"
    try:
        return type(module.get_keyring()).__name__
    except Exception as error:
        return f"unavailable ({type(error).__name__})"


def lookup(name: str) -> str | None:
    module = _keyring()
    if module is None:
        return None
    try:
        return module.get_password(SERVICE, name) or None
    except Exception:
        return None


def store(name: str, key: str) -> None:
    module = _keyring()
    if module is None:
        raise CredentialError("The keyring package is not installed, so the key cannot be saved.")
    try:
        module.set_password(SERVICE, name, key)
    except Exception as error:
        raise CredentialError(
            f"The key for {name} could not be saved by {backend_name()}: "
            f"{type(error).__name__}: {error}"
        ) from error


def forget(name: str) -> bool:
    module = _keyring()
    if module is None:
        return False
    try:
        module.delete_password(SERVICE, name)
    except Exception:
        return False
    return True
