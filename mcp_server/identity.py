"""Résolution du profil appelant — jamais un paramètre de tool (E4).

Pour la démo, le contrat d'intégration (``tests/conftest.py``, ``README.md``) résout le
profil depuis la variable d'environnement ``SORABEL_PROFILE``, lue une fois par process
serveur (un process par client interne). ``IdentityResolver`` est un ``Protocol`` — même
patron que ``AccessRules``/``TraceRecorder`` — pour qu'un vrai IdP (OAuth 2.0/OIDC, voir
conception/3_MCP/questions_reponses_mcp.md § 1.5) soit substituable plus tard sans
modifier ``mcp_server/server.py`` (spec_mcp.md § 4.2). Non implémenté ici : hors
périmètre de ce chantier, non exigé par le brief ni par le contrat de test fourni.
"""

from __future__ import annotations

import os
from typing import Protocol

from sql.access import PROFILES

DEFAULT_PROFILE = "support"


class IdentityResolver(Protocol):
    def resolve(self) -> str: ...


class EnvVarIdentityResolver:
    """Lit ``SORABEL_PROFILE`` (défaut ``support``) — un process serveur par client."""

    def resolve(self) -> str:
        profile = os.environ.get("SORABEL_PROFILE", DEFAULT_PROFILE)
        if profile not in PROFILES:
            raise ValueError(f"profil inconnu : {profile!r}")
        return profile
