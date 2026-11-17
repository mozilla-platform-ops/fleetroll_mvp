"""Resolve recorded reference inputs, then use the shared pure evaluator."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from ...constants import DEFAULT_GITHUB_REPO
from ...verification import evaluate_verification
from .cache import override_configuration_key, parse_override_content

if TYPE_CHECKING:
    from .cache import ShaInfoCache


def expected_reference(
    record: dict | None, *, refs: dict, sha_cache: ShaInfoCache | None
) -> dict | None:
    envelope = ((record or {}).get("observed") or {}).get("configuration_evidence") or {}
    override = (envelope.get("components") or {}).get("override") or {}
    source, repo, branch = "default_master", DEFAULT_GITHUB_REPO.lower(), "master"
    sha = override.get("sha256")
    sha = sha.lower() if isinstance(sha, str) else sha
    if override.get("status") == "present":
        if (
            sha_cache is None
            or not isinstance(sha, str)
            or len(sha) != 64
            or any(c not in "0123456789abcdef" for c in sha)
        ):
            return None
        details = None
        for path in sorted(sha_cache.overrides_dir.glob(f"{sha[:12]}*")):
            try:
                content = path.read_bytes()
                if len(content) > 1_048_576 or hashlib.sha256(content).hexdigest() != sha:
                    continue
                text = content.decode("utf-8")
            except (OSError, UnicodeError):
                continue
            if override_configuration_key(text) is not None:
                details = parse_override_content(text)
            break
        if not details or not all(details.get(key) for key in ("user", "repo", "branch")):
            return None
        repo, branch = f"{details['user']}/{details['repo']}".lower(), details["branch"]
        source = "override_artifact"
    elif override.get("status") != "absent":
        return None
    key = f"{repo}:{branch}"
    # Repository names are case insensitive; branch names are not.
    ref = next(
        (
            value
            for name, value in refs.items()
            if name.rpartition(":")[0].lower() == repo and name.rpartition(":")[2] == branch
        ),
        None,
    )
    if not ref:
        return None
    if any(
        ref.get(field) is not None
        and (
            not isinstance(ref[field], str)
            or (ref[field] if field == "branch" else ref[field].lower()) != value
        )
        for field, value in (
            ("owner", repo.split("/")[0]),
            ("repo", repo.split("/")[1]),
            ("branch", branch),
        )
    ):
        return None
    return {
        "repository": repo,
        "branch": branch,
        "sha": ref.get("sha"),
        "ts": ref.get("ts"),
        "source": source,
        "ref_key": key,
        "override_sha256": sha if source == "override_artifact" else None,
    }


def evaluate_host(
    record: dict | None,
    *,
    now: datetime,
    refs: dict | None = None,
    sha_cache: ShaInfoCache | None = None,
    tc_record: dict | None = None,
    lookup_attempt: dict | None = None,
) -> dict[str, Any]:
    return evaluate_verification(
        record,
        now=now,
        expected_reference=expected_reference(record, refs=refs or {}, sha_cache=sha_cache),
        tc_record=tc_record,
        lookup_attempt=lookup_attempt,
    )


def evaluate_hosts(
    hosts: list[str],
    *,
    latest: dict,
    tc_data: dict,
    refs: dict,
    sha_cache: ShaInfoCache | None,
    lookup_attempts: dict | None = None,
    now: datetime | None = None,
) -> dict[str, dict[str, Any]]:
    now = now or datetime.now(UTC)
    return {
        host: evaluate_host(
            latest.get(host),
            now=now,
            refs=refs,
            sha_cache=sha_cache,
            tc_record=tc_data.get(host) or tc_data.get(host.split(".")[0]),
            lookup_attempt=(lookup_attempts or {}).get(host),
        )
        for host in hosts
    }


def comparison_record(record: dict) -> dict:
    """Follow mode reacts to evidence/status transitions instead of ticking ages."""

    def stable(value):
        if isinstance(value, dict):
            return {
                key: stable(item)
                for key, item in value.items()
                if key not in {"evaluated_at", "age_seconds"}
            }
        if isinstance(value, list):
            return [stable(item) for item in value]
        return value

    return {**record, "verification": stable(record.get("verification"))}
