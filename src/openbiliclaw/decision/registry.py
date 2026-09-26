"""Build explicitly enabled decision providers without borrowing chat credentials."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from openbiliclaw.config import ConfigError, validate_jev_decision_config

from .jev_provider import JevDecisionProvider

if TYPE_CHECKING:
    from collections.abc import Mapping

    import httpx

    from openbiliclaw.config import DecisionConfig

    from .base import DecisionProvider


class DecisionConfigurationError(ConfigError):
    """An explicitly requested decision provider cannot be constructed safely."""


def build_decision_provider(
    config: DecisionConfig,
    *,
    provider_name: str = "jev",
    client: httpx.AsyncClient | None = None,
    environ: Mapping[str, str] | None = None,
) -> DecisionProvider | None:
    """Build one enabled provider, or return None without credentials/network work.

    This factory does not register a chat provider or enable a recommendation
    route. The caller owns the resulting provider's lifetime. An injected HTTP
    client remains caller-owned even after the provider is closed.
    """
    if provider_name != "jev":
        raise DecisionConfigurationError("Unsupported decision provider; expected jev.")
    settings = config.providers.jev
    validate_jev_decision_config(settings)
    if not settings.enabled:
        return None

    variables = os.environ if environ is None else environ
    raw_key = variables.get(settings.api_key_env, "")
    api_key = raw_key.strip()
    if not api_key:
        raise DecisionConfigurationError(
            "Enabled decision.providers.jev requires the environment variable named by api_key_env."
        )
    # HTTP header syntax, not a vendor-specific key format. Never include the
    # received value in exceptions or diagnostics.
    if not api_key.isascii() or any(ord(char) < 33 or ord(char) == 127 for char in api_key):
        raise DecisionConfigurationError("The Jev credential is not a valid bearer token.")
    return JevDecisionProvider(
        api_key=api_key,
        model=settings.model,
        timeout_seconds=settings.timeout_seconds,
        max_attempts=settings.max_attempts,
        client=client,
    )
