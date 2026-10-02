"""Load validated routing profiles and derive exact-scope policy priors."""
from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any, Mapping

from ..control.policy_selector import BehaviorPolicyPrior
from ..feedback_loop import trusted_state_root
from ..routing_profiles import (
    BehavioralRoutingProfile,
    RoutingProfileError,
    SharingDecisionAuthority,
    applicable_adjustments,
    validate_profile,
)


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


class BehaviorPolicyPriorStore:
    """Own profile trust validation, caching, and bounded routing advice."""

    def __init__(self, *, profile_path: Path | None,
                 sharing_authority: SharingDecisionAuthority | None = None) -> None:
        self.profile_path = profile_path
        self.sharing_authority = sharing_authority
        self._cache_key: tuple[int, int, int, int, int] | None = None
        self._cached_priors: tuple[BehaviorPolicyPrior, ...] = ()

    def policy_priors(self, policy_context: Mapping[str, Any]
                      ) -> tuple[BehaviorPolicyPrior, ...]:
        """Return exact-scope priors from active, validated profiles only."""
        if not policy_context:
            return ()
        path = self.profile_path or (trusted_state_root() / "active-routing-profile.json")
        try:
            info = path.lstat()
            cache_key = (info.st_ino, info.st_size, info.st_mtime_ns,
                         info.st_mode, info.st_uid)
        except OSError:
            return ()
        if cache_key == self._cache_key:
            return tuple(item for item in self._cached_priors
                         if item.matches(policy_context))
        try:
            profiles, _profile_hash = self.load_profiles()
        except (RoutingProfileError, OSError, TypeError, ValueError):
            self._cache_key = cache_key
            self._cached_priors = ()
            return ()
        priors = []
        for profile in profiles:
            priors.extend(BehaviorPolicyPrior.from_promoted_profile(
                profile, sharing_authority=self.sharing_authority))
        self._cache_key = cache_key
        self._cached_priors = tuple(priors[:64])
        return tuple(item for item in self._cached_priors
                     if item.matches(policy_context))

    def load_profiles(self) -> tuple[list[dict[str, Any]], str]:
        path = self.profile_path
        if path is None:
            path = trusted_state_root() / "active-routing-profile.json"
        if not path.exists():
            return [], ""
        if path.is_symlink():
            raise RoutingProfileError("routing profile path must not be a symlink")
        info = path.stat()
        if info.st_uid != os.getuid() or info.st_mode & 0o022:
            raise RoutingProfileError(
                "routing profile artifact must be owned by the service user and not writable by others")
        parent = path.parent
        while True:
            parent_info = parent.lstat()
            shared_sticky_dir = (stat.S_ISDIR(parent_info.st_mode)
                                 and bool(parent_info.st_mode & stat.S_ISVTX))
            if stat.S_ISLNK(parent_info.st_mode) or (
                    parent_info.st_mode & 0o022 and not shared_sticky_dir):
                raise RoutingProfileError(
                    "routing profile parent directory is not a protected trust boundary")
            if parent == parent.parent:
                break
            parent = parent.parent
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, Mapping) and isinstance(data.get("profile"), Mapping):
            profiles = [dict(data["profile"])]
            revision = data.get("revision")
            if profiles[0].get("lifecycle_revision") != revision:
                raise RoutingProfileError("active routing profile revision mismatch")
            current = profiles[0]
            lifecycle_status = current.get("lifecycle_status") or current.get("status")
            current_unavailable = (current.get("status") in {"stale", "rolled_back"}
                                   or lifecycle_status in {"stale", "rolled_back"})
            if not current_unavailable:
                try:
                    validate_profile(current, require_deployable=True,
                                     sharing_authority=self.sharing_authority)
                except RoutingProfileError:
                    current_unavailable = True
            if current_unavailable:
                known_good = data.get("known_good_profile")
                if (not isinstance(known_good, Mapping)
                        or known_good.get("profile_hash") == current.get("profile_hash")
                        or known_good.get("status") not in {"active", "validated"}
                        or known_good.get("lifecycle_status") not in {"active", "validated"}):
                    return [], ""
                validate_profile(dict(known_good), require_deployable=True,
                                 sharing_authority=self.sharing_authority)
                profiles = [dict(known_good)]
        elif isinstance(data, Mapping) and isinstance(data.get("profiles"), list):
            profiles = [dict(item) for item in data["profiles"]
                        if item.get("status") not in {"stale", "rolled_back"}
                        and item.get("lifecycle_status") not in {"stale", "rolled_back"}]
        elif isinstance(data, Mapping):
            profiles = [dict(data)]
        else:
            raise RoutingProfileError("routing profile artifact must be an object")
        for profile in profiles:
            validate_profile(profile, require_deployable=True,
                             sharing_authority=self.sharing_authority)
        pack_hash = hashlib.sha256(_canonical(profiles).encode("utf-8")).hexdigest()
        return profiles, pack_hash

    @staticmethod
    def eligible_route_keys(routes: list[Any]) -> set[tuple[str, str]]:
        result: set[tuple[str, str]] = set()
        for route in routes:
            if isinstance(route, str):
                result.add(("*", route))
            elif isinstance(route, Mapping):
                route_type = str(route.get("route_type") or "*")
                name = str(route.get("route") or "")
                if name:
                    result.add((route_type, name))
        return result

    def behavioral_advice(self, *, context: Mapping[str, Any],
                          eligible_routes: list[Any],
                          static_policy: Mapping[str, Any]) -> dict[str, Any]:
        """Return bounded advice only for host-declared eligible routes."""
        try:
            profiles, profile_hash = self.load_profiles()
        except (RoutingProfileError, OSError, TypeError, ValueError) as exc:
            return {"status": "unavailable", "reason": str(exc),
                    "adjustments": [], "dp_revision": None}
        allowed = self.eligible_route_keys(eligible_routes)
        adjustments: list[dict[str, Any]] = []
        sources: list[str] = []
        for raw in profiles:
            try:
                profile = BehavioralRoutingProfile.from_dict(
                    raw, require_deployable=True,
                    sharing_authority=self.sharing_authority)
                matches = applicable_adjustments(
                    profile, context, sharing_authority=self.sharing_authority)
            except RoutingProfileError:
                continue
            for adjustment in matches:
                key = (str(adjustment.get("route_type") or "*"),
                       str(adjustment.get("route") or ""))
                if adjustment.get("disposition") not in {"prefer", "suppress"}:
                    continue
                if key not in allowed and ("*", key[1]) not in allowed:
                    continue
                route_adjustment = {key: value for key, value in adjustment.items()
                                    if key != "behavior_policy"}
                adjustments.append({**route_adjustment,
                                    "profile_id": raw.get("profile_id"),
                                    "profile_hash": raw.get("profile_hash")})
                sources.append(str(raw.get("profile_id") or raw.get("profile_hash")))
        body = {
            "status": "ok",
            "static_policy_hash": static_policy.get("policy_hash", static_policy.get("static_policy_hash")),
            "eligible_choice_hash": static_policy.get("eligible_choice_hash"),
            "context_identity": static_policy.get("context_identity"),
            "profile_pack_hash": profile_hash,
            "adjustments": adjustments,
            "sources": sorted(set(sources)),
            "expires_at": min((str(profile.get("expires_at")) for profile in profiles
                               if profile.get("expires_at")), default=None),
            "dp_revision": self.profile_revision(),
        }
        body["semantic_hash"] = hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()
        body["advice_id"] = f"advice-{body['semantic_hash'][:20]}"
        return body

    def validate_profile(self, profile: Mapping[str, Any]) -> dict[str, Any]:
        validate_profile(profile, require_deployable=True,
                         sharing_authority=self.sharing_authority)
        return {"valid": True, "profile_id": profile["profile_id"],
                "profile_hash": profile["profile_hash"], "scope_mode": profile["scope_mode"]}

    def profile_revision(self) -> int | None:
        path = self.profile_path
        if path is None:
            path = trusted_state_root() / "active-routing-profile.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return int(data.get("revision")) if data.get("revision") is not None else None
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None
