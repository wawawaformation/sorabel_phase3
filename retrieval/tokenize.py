"""Tokenisation pour BM25.

Repli des diacritiques : le corpus est en français, « triphasé » et « triphase » doivent
donner le même jeton. Vérifié sur le corpus réel (spec § 2.5).
"""

import re
import unicodedata

RE_TOKEN = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    folded = unicodedata.normalize("NFKD", text.lower())
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    return RE_TOKEN.findall(folded)
