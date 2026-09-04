import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

ISSUER = "http://localhost:18080/realms/sorabel"
AUDIENCE = "sorabel-gateway"


@pytest.fixture(scope="module")
def rsa_keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


def _jwk(public_key, kid: str) -> dict:
    jwk = RSAAlgorithm.to_jwk(public_key, as_dict=True)
    jwk["kid"] = kid
    jwk["alg"] = "RS256"
    jwk["use"] = "sig"
    return jwk


def _token(private_key, kid: str, *, roles: list[str], azp: str = AUDIENCE,
           exp_delta: int = 300, issuer: str = ISSUER) -> str:
    claims = {
        "sub": "u1", "iss": issuer, "azp": azp,
        "exp": int(time.time()) + exp_delta,
        "realm_access": {"roles": roles},
    }
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": kid})


class FakeJWKClient:
    """Remplace jwt.PyJWKClient : pas d'appel réseau, une seule clé connue."""

    def __init__(self, public_key, kid: str) -> None:
        self._jwk = jwt.PyJWK(_jwk(public_key, kid), algorithm="RS256")
        self._kid = kid

    def get_signing_key_from_jwt(self, token: str):
        header = jwt.get_unverified_header(token)
        if header.get("kid") != self._kid:
            raise jwt.exceptions.PyJWKClientError("kid inconnu")
        return self._jwk


# --- resolve_profile() : pur, sans réseau ---

def test_resolve_profile_commercial():
    from mcp_server.keycloak_auth import resolve_profile

    assert resolve_profile({"realm_access": {"roles": ["commercial", "offline_access"]}}) == "commercial"


def test_resolve_profile_support():
    from mcp_server.keycloak_auth import resolve_profile

    assert resolve_profile({"realm_access": {"roles": ["support"]}}) == "support"


def test_resolve_profile_aucun_role_connu():
    from mcp_server.keycloak_auth import resolve_profile

    assert resolve_profile({"realm_access": {"roles": ["offline_access"]}}) is None


def test_resolve_profile_les_deux_roles_ambigu():
    from mcp_server.keycloak_auth import resolve_profile

    assert resolve_profile({"realm_access": {"roles": ["commercial", "support"]}}) is None


def test_resolve_profile_claims_sans_realm_access():
    from mcp_server.keycloak_auth import resolve_profile

    assert resolve_profile({}) is None


# --- KeycloakTokenVerifier : signature, expiration, azp, kid ---

async def test_verifier_accepte_un_token_valide(rsa_keypair):
    from mcp_server.keycloak_auth import KeycloakTokenVerifier

    private_key, public_key = rsa_keypair
    token = _token(private_key, "kid1", roles=["commercial"])
    verifier = KeycloakTokenVerifier(FakeJWKClient(public_key, "kid1"), ISSUER, AUDIENCE)

    access_token = await verifier.verify_token(token)
    assert access_token is not None
    assert access_token.subject == "u1"
    assert access_token.claims["realm_access"]["roles"] == ["commercial"]


async def test_verifier_rejette_un_token_expire(rsa_keypair):
    from mcp_server.keycloak_auth import KeycloakTokenVerifier

    private_key, public_key = rsa_keypair
    token = _token(private_key, "kid1", roles=["commercial"], exp_delta=-10)
    verifier = KeycloakTokenVerifier(FakeJWKClient(public_key, "kid1"), ISSUER, AUDIENCE)

    assert await verifier.verify_token(token) is None


async def test_verifier_rejette_une_signature_invalide(rsa_keypair):
    from mcp_server.keycloak_auth import KeycloakTokenVerifier

    private_key, public_key = rsa_keypair
    autre_cle = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = _token(autre_cle, "kid1", roles=["commercial"])
    verifier = KeycloakTokenVerifier(FakeJWKClient(public_key, "kid1"), ISSUER, AUDIENCE)

    assert await verifier.verify_token(token) is None


async def test_verifier_rejette_un_issuer_incorrect(rsa_keypair):
    from mcp_server.keycloak_auth import KeycloakTokenVerifier

    private_key, public_key = rsa_keypair
    token = _token(private_key, "kid1", roles=["commercial"], issuer="http://ailleurs/realms/x")
    verifier = KeycloakTokenVerifier(FakeJWKClient(public_key, "kid1"), ISSUER, AUDIENCE)

    assert await verifier.verify_token(token) is None


async def test_verifier_rejette_un_azp_incorrect(rsa_keypair):
    # azp, pas aud : Keycloak n'émet pas de claim "aud" par défaut pour un client
    # public sans mapper dédié (vérifié empiriquement contre un vrai Keycloak 26).
    from mcp_server.keycloak_auth import KeycloakTokenVerifier

    private_key, public_key = rsa_keypair
    token = _token(private_key, "kid1", roles=["commercial"], azp="un-autre-client")
    verifier = KeycloakTokenVerifier(FakeJWKClient(public_key, "kid1"), ISSUER, AUDIENCE)

    assert await verifier.verify_token(token) is None


async def test_verifier_rejette_un_kid_inconnu(rsa_keypair):
    from mcp_server.keycloak_auth import KeycloakTokenVerifier

    private_key, public_key = rsa_keypair
    token = _token(private_key, "kid-inattendu", roles=["commercial"])
    verifier = KeycloakTokenVerifier(FakeJWKClient(public_key, "kid1"), ISSUER, AUDIENCE)

    assert await verifier.verify_token(token) is None
