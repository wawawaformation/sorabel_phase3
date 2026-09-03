import pytest

from sql.access import PROFILES, SENSITIVE_COLUMNS, StaticAccessRules


def test_support_ne_voit_pas_les_colonnes_sensibles():
    rules = StaticAccessRules()
    cachees = rules.hidden_columns("support")
    assert ("produits", "prix_achat_ht") in cachees
    assert ("produits", "marge_pct") in cachees
    assert ("ventes", "marge_ht") in cachees
    assert len(cachees) == 3


def test_commercial_ne_subit_aucune_restriction():
    assert StaticAccessRules().hidden_columns("commercial") == frozenset()


def test_profil_inconnu_refuse_plutot_que_de_tout_ouvrir():
    # Un profil non prévu ne doit jamais aboutir à un accès complet par défaut :
    # le comportement sûr est l'erreur, pas le silence permissif.
    with pytest.raises(ValueError, match="profil inconnu"):
        StaticAccessRules().hidden_columns("admin")


def test_profils_et_colonnes_sensibles_exposes_comme_constantes():
    # Les tests d'intégration et l'eval s'appuient sur ces constantes plutôt que
    # de recopier la liste — une seule source de vérité (conception § 3.3).
    assert PROFILES == frozenset({"support", "commercial"})
    assert ("ventes", "marge_ht") in SENSITIVE_COLUMNS
