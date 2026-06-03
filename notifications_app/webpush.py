from __future__ import annotations

import base64
import importlib
import json
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def _base64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _public_key_from_private_key(private_key_pem: str) -> str:
    private_key = serialization.load_pem_private_key(
        private_key_pem.encode("utf-8"),
        password=None,
    )
    public_numbers = private_key.public_key().public_numbers()
    public_key = b"\x04" + public_numbers.x.to_bytes(32, "big") + public_numbers.y.to_bytes(32, "big")
    return _base64url(public_key)


def _generate_vapid_private_key() -> bytes:
    private_key = ec.generate_private_key(ec.SECP256R1())
    return private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def ensure_vapid_keys(instance_path: Path, subject: str) -> dict[str, str]:
    instance_path.mkdir(parents=True, exist_ok=True)
    private_key_path = instance_path / "vapid_private_key.pem"

    private_key_pem = None
    if private_key_path.exists():
        try:
            private_key_pem = private_key_path.read_text(encoding="utf-8")
            serialization.load_pem_private_key(private_key_pem.encode("utf-8"), password=None)
        except Exception:
            private_key_pem = None

    if private_key_pem is None:
        private_key_pem = _generate_vapid_private_key().decode("utf-8")
        private_key_path.write_text(private_key_pem, encoding="utf-8")

    return {
        "public_key": _public_key_from_private_key(private_key_pem),
        "private_key": private_key_pem,
        "private_key_path": str(private_key_path),
        "subject": subject,
    }


def _load_pywebpush() -> tuple[Any | None, type[Exception]]:
    try:
        module = importlib.import_module("pywebpush")
    except ModuleNotFoundError:
        return None, Exception

    return getattr(module, "webpush", None), getattr(module, "WebPushException", Exception)


def send_web_push(
    *,
    subscription_info: dict[str, Any],
    vapid_private_key_path: str,
    vapid_subject: str,
    payload: dict[str, Any],
) -> bool:
    webpush_function, webpush_exception = _load_pywebpush()
    if webpush_function is None:
        raise RuntimeError(
            "pywebpush is not installed. Install requirements.txt in a clean environment to enable push delivery."
        )

    try:
        webpush_function(
            subscription_info=subscription_info,
            data=json.dumps(payload, separators=(",", ":"), ensure_ascii=False),
            vapid_private_key=vapid_private_key_path,
            vapid_claims={"sub": vapid_subject},
            ttl=300,
        )
        return True
    except webpush_exception as exc:
        status_code = getattr(getattr(exc, "response", None), "status_code", None)
        if status_code in {404, 410}:
            return False
        raise


