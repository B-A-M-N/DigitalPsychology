"""Host attestation checks for behavioral event producers."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from typing import Any, Mapping


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


class EventAttestationAuthority:
    """Verify producer scope and signatures before telemetry is persisted."""

    def __init__(self, *, trusted_producers: Mapping[str, Mapping[str, Any]] | None = None,
                 legacy_secret: bytes = b"") -> None:
        self.trusted_producers = {
            str(key): dict(value) for key, value in (trusted_producers or {}).items()}
        self.legacy_secret = bytes(legacy_secret)

    def _producer_key(self, key_id: str) -> bytes:
        raw = os.environ.get(f"DIGITALPSYCHOLOGY_INGESTION_KEY_{key_id}", "")
        if not raw or not self.trusted_producers:
            raise ValueError("behavior event producer key is unavailable")
        return raw.encode()

    def attest(self, data: Mapping[str, Any],
               attestation: Mapping[str, Any] | None) -> bool:
        privileged = (data.get("category") in {"validation", "state_transition", "completion"}
                      or data.get("actor_id") in {
                          "operator", "repository_owner", "service_owner"})
        producer_key = "|".join((str(data.get("namespace_id") or ""),
                                 str(data.get("application_instance_id") or ""),
                                 str(data.get("agent_instance_id") or "")))
        producer = self.trusted_producers.get(producer_key)
        if producer is not None:
            if not isinstance(attestation, Mapping):
                raise ValueError("configured producer requires host attestation")
            key_id = str(attestation.get("key_id") or "")
            if not key_id or key_id != str(producer.get("key_id") or ""):
                raise ValueError("behavior event producer key identifier is unauthorized")
            if attestation.get("issuer") != producer.get("issuer"):
                raise ValueError("behavior event attestation issuer is invalid")
            if data.get("actor_id") not in set(producer.get("allowed_actors", ())):
                raise ValueError("behavior event actor is not producer-authorized")
            if data.get("category") not in set(producer.get("allowed_categories", ())):
                raise ValueError("behavior event category is not producer-authorized")
            if data.get("application_id") != producer.get("application_id"):
                raise ValueError("behavior event application is not producer-authorized")
            body = {key: value for key, value in data.items()
                    if key != "host_attestation"}
            expected = hmac.new(
                self._producer_key(key_id), _canonical(body).encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(str(attestation.get("signature") or ""), expected):
                raise ValueError("behavior event host attestation is invalid")
            return True
        if self.trusted_producers:
            raise ValueError("behavior event producer is not configured")
        if not self.legacy_secret:
            if privileged:
                raise ValueError("privileged behavior events require host attestation")
            return False
        if not isinstance(attestation, Mapping):
            raise ValueError("behavior event host attestation is required")
        body = {key: value for key, value in data.items()
                if key != "host_attestation"}
        expected = hmac.new(
            self.legacy_secret, _canonical(body).encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(str(attestation.get("signature") or ""), expected):
            raise ValueError("behavior event host attestation is invalid")
        return True


__all__ = ["EventAttestationAuthority"]
