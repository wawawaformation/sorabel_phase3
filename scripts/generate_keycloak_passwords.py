"""Génère des mots de passe de démo pour le royaume Keycloak — à coller dans `.env`.

Alphanumériques uniquement, volontairement : docker/keycloak/render-realm.sh les
injecte via `sed`, où un caractère `&` dans le remplacement serait interprété comme
« le motif trouvé » et corromprait silencieusement le mot de passe (vérifié
empiriquement). Pas de dépendance à une longueur de caractères spéciaux pour la
robustesse : 24 caractères aléatoires suffisent largement pour un compte de démo.

Usage : ``uv run python scripts/generate_keycloak_passwords.py``
"""

import secrets
import string

ALPHABET = string.ascii_letters + string.digits
LENGTH = 24


def generate() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(LENGTH))


def main() -> None:
    print(f"KEYCLOAK_COMMERCIAL_PASSWORD={generate()}")
    print(f"KEYCLOAK_SUPPORT_PASSWORD={generate()}")


if __name__ == "__main__":
    main()
