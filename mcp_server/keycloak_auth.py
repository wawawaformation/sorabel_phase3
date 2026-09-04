"""Validation JWT Keycloak et résolution du profil Sorabel — chantier déploiement HTTP.

``resolve_profile`` est une fonction pure (pas de réseau, pas d'objet Keycloak) :
elle lit ``realm_access.roles`` et retourne le profil Sorabel s'il y en a exactement
un reconnu, ``None`` sinon (rôle absent ou ambigu — un jeton valide mais dont
l'identité ne peut pas être rattachée à un profil, pas une erreur d'authentification).

``KeycloakTokenVerifier`` implémente le ``Protocol TokenVerifier`` du SDK MCP
(``mcp.server.auth.provider``) : signature JWT validée contre le JWKS Keycloak (caché
par ``jwt.PyJWKClient``, pas un appel réseau par requête), expiration, issuer, et
``azp`` (authorized party) — pas ``aud`` : Keycloak n'émet pas de claim ``aud`` par
défaut pour un client public sans mapper d'audience dédié (vérifié empiriquement
contre un vrai Keycloak 26.7.3, spec_deploiement.md § corrections empiriques).
"""

from __future__ import annotations

import jwt
from mcp.server.auth.provider import AccessToken, TokenVerifier

from sql.access import PROFILES

#: Réciproque : ce module ne connaît qu'un rôle réalm par profil Sorabel, mapping
#: direct (conception § 3.2 de la vue générale : « IDE dev passe par
#: Commercial/Support », pas un troisième profil).
_KNOWN_ROLES = frozenset(PROFILES)


def resolve_profile(claims: dict) -> str | None:
    roles = set(claims.get("realm_access", {}).get("roles", []))
    matched = roles & _KNOWN_ROLES
    if len(matched) != 1:
        return None
    return next(iter(matched))


class KeycloakTokenVerifier(TokenVerifier):
    def __init__(self, jwks_client, issuer: str, audience: str) -> None:
        self._jwks_client = jwks_client
        self._issuer = issuer
        self._audience = audience

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            signing_key = self._jwks_client.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token, signing_key.key, algorithms=["RS256"], issuer=self._issuer,
                options={"verify_aud": False},
            )
        except jwt.PyJWTError:
            return None
        if claims.get("azp") != self._audience:
            return None
        return AccessToken(
            token=token, client_id=claims["azp"], scopes=[], expires_at=claims["exp"],
            subject=claims.get("sub"), claims=claims,
        )
