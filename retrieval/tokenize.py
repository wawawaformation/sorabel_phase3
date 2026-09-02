"""Tokenisation pour BM25 (retrieval/lexical.py).

Repli des diacritiques : le corpus est en français, « triphasé » et « triphase » doivent
donner le même jeton. Vérifié sur le corpus réel (spec § 2.5). La même fonction est
appliquée à la fois à l'indexation des chunks et à la question posée par l'utilisateur,
ce qui garantit que les deux passent par exactement le même repliage.
"""

import re
import unicodedata

RE_TOKEN = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    """Normalise puis découpe un texte en jetons alphanumériques minuscules.

    Trois étapes : décomposition Unicode NFKD (sépare une lettre accentuée en
    lettre de base + diacritique combinant), suppression des diacritiques,
    puis extraction des séquences ``[a-z0-9]+`` — la ponctuation et les espaces
    deviennent des séparateurs, jamais des jetons.
    """
    folded = unicodedata.normalize("NFKD", text.lower())
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    return RE_TOKEN.findall(folded)
