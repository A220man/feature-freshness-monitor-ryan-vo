"""Signed HTTP webhook alert dispatcher for feature incident transitions.

Target URLs must use HTTPS and resolve exclusively to public global IP
addresses to prevent server-side request forgery (SSRF). Payloads are signed
using HMAC-SHA256 over canonical JSON.
"""
import hashlib
import hmac
import ipaddress
import json
import socket
from typing import Callable
from urllib.parse import urlsplit
import httpx
from app.domain.freshness import IncidentTransition, utc


WebhookSender = Callable[[str, bytes, dict[str, str]], tuple[int, str]]

FORBIDDEN_HOSTS = {"localhost", "localhost.localdomain"}
FORBIDDEN_SUFFIXES = (".local", ".localhost", ".internal", ".arpa")


def _check_ip_global(address: str) -> None:
    ip = ipaddress.ip_address(address)
    if (
        not ip.is_global
        or ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    ):
        raise ValueError("webhook URL must not target non-global IP addresses")


def validate_webhook_url(target_url: str) -> str:
    if not isinstance(target_url, str):
        raise ValueError("webhook URL is required")
    cleaned = target_url.strip()
    if not cleaned or len(cleaned) > 500 or any(ord(c) < 33 for c in cleaned):
        raise ValueError("webhook URL must be 1..500 printable characters")
    parsed = urlsplit(cleaned)
    if parsed.scheme != "https" or not parsed.netloc or not parsed.hostname:
        raise ValueError("webhook URL must use https with a valid hostname")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("webhook URL must not embed credentials")
    host = parsed.hostname.lower().rstrip(".")
    if host in FORBIDDEN_HOSTS or host.endswith(FORBIDDEN_SUFFIXES):
        raise ValueError("webhook URL must not target local or internal hosts")
    try:
        _check_ip_global(host)
    except ValueError as err:
        if "non-global" in str(err):
            raise
    return cleaned


def validate_signing_secret(secret: str) -> str:
    if not isinstance(secret, str):
        raise ValueError("webhook signing secret is required")
    cleaned = secret.strip()
    if len(cleaned) < 8 or len(cleaned) > 200 or any(ord(c) < 32 for c in cleaned):
        raise ValueError("webhook signing secret must be 8..200 printable characters")
    return cleaned


def sign_webhook_payload(payload_bytes: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def verify_webhook_signature(payload_bytes: bytes, secret: str, signature: str) -> bool:
    expected = sign_webhook_payload(payload_bytes, secret)
    return hmac.compare_digest(expected, signature)


def build_transition_payload(webhook_id: str, transition: IncidentTransition) -> dict:
    return {
        "action": transition.action,
        "condition": transition.condition,
        "feature_set": transition.feature_set,
        "incident_id": transition.incident_id,
        "occurred_at": utc(transition.occurred_at).isoformat(timespec="microseconds"),
        "partition": transition.partition,
        "webhook_id": webhook_id,
    }


def default_http_sender(target_url: str, payload_bytes: bytes, headers: dict[str, str]) -> tuple[int, str]:
    validated = validate_webhook_url(target_url)
    parsed = urlsplit(validated)
    host = parsed.hostname or ""
    port = parsed.port or 443
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise RuntimeError("unable to resolve webhook destination") from exc
    if not infos:
        raise RuntimeError("unable to resolve webhook destination")
    for info in infos:
        sockaddr = info[4]
        _check_ip_global(sockaddr[0])
    with httpx.Client(timeout=5.0, follow_redirects=False) as client:
        response = client.post(validated, content=payload_bytes, headers=headers)
        return response.status_code, f"HTTP {response.status_code}"


def dispatch_webhook(
    target_url: str,
    secret: str,
    payload: dict,
    sender: WebhookSender = default_http_sender,
) -> tuple[str, str, str, int | None, str]:
    payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    payload_bytes = payload_json.encode("utf-8")
    signature = sign_webhook_payload(payload_bytes, secret)
    headers = {
        "Content-Type": "application/json",
        "X-Freshness-Event": f"incident.{payload.get('action', 'transition')}",
        "X-Freshness-Signature": signature,
    }
    try:
        status_code, detail = sender(target_url, payload_bytes, headers)
        if 200 <= status_code < 300:
            return payload_json, signature, "delivered", status_code, detail or f"HTTP {status_code}"
        return payload_json, signature, "failed", status_code, detail or f"HTTP {status_code}"
    except Exception as exc:
        reason = str(exc) if isinstance(exc, ValueError) else "webhook delivery failed"
        return payload_json, signature, "failed", None, reason
