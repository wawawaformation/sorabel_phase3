#!/bin/sh
# Substitue les mots de passe de démo (venus de l'environnement, jamais du fichier
# versionné) dans le royaume avant import. Écrit le résultat hors du volume monté
# depuis l'hôte (/opt/keycloak/data/import, pas .../import-template) : le fichier
# rendu, qui contient les vrais mots de passe, ne touche jamais le disque de l'hôte
# ni git.
#
# sed, pas envsubst (absent de cette image) : attention, & dans une valeur de
# remplacement sed est interprété comme « le motif trouvé » — vérifié empiriquement,
# ça corrompt silencieusement le mot de passe sans erreur visible. D'où des mots de
# passe générés uniquement alphanumériques (scripts/generate_keycloak_passwords.py).
set -e

mkdir -p /opt/keycloak/data/import
sed \
  -e "s|__COMMERCIAL_DEMO_PASSWORD__|${KEYCLOAK_COMMERCIAL_PASSWORD}|g" \
  -e "s|__SUPPORT_DEMO_PASSWORD__|${KEYCLOAK_SUPPORT_PASSWORD}|g" \
  /opt/keycloak/data/import-template/sorabel-realm.json.template \
  > /opt/keycloak/data/import/sorabel-realm.json

exec /opt/keycloak/bin/kc.sh start-dev --import-realm
