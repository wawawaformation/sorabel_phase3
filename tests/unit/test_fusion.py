from retrieval.fusion import reciprocal_rank_fusion, rrf_scores


def test_rrf_scores_expose_les_valeurs_brutes():
    # a : 1er dans une seule liste -> 1/61. b : 2e dans les deux -> 1/62 + 1/62.
    scores = rrf_scores([["a", "b"], ["c", "b"]])
    assert scores["a"] == 1 / 61
    assert scores["b"] == 2 / 62


def test_document_present_dans_les_deux_classements_remonte():
    # « b » est 2e partout ; « a » est 1er dans A mais absent de B.
    # Somme RRF (k=60) : b = 1/61 + 1/61 ≈ 0.0328 > a = 1/61 ≈ 0.0164
    assert reciprocal_rank_fusion([["a", "b"], ["c", "b"]])[0] == "b"


def test_rangs_pris_en_compte_pas_les_scores():
    # Aucun score n'est fourni : seule la position compte (c'est l'intérêt de RRF,
    # les scores vectoriels et BM25 n'étant pas sur la même échelle).
    assert reciprocal_rank_fusion([["a", "b", "c"]]) == ["a", "b", "c"]


def test_limite_respectee():
    assert reciprocal_rank_fusion([["a", "b", "c"]], limit=2) == ["a", "b"]


def test_classements_vides():
    assert reciprocal_rank_fusion([[], []]) == []


def test_k_modifie_le_poids_relatif_des_rangs():
    # « z » : 1er dans une seule liste. « w » : 3e dans les deux listes.
    rankings = [["z", "y", "w"], ["x", "q", "w"]]
    # k=0 : le rang exact domine (1/1=1.0 pour z, contre 1/3+1/3≈0.67 pour w).
    assert reciprocal_rank_fusion(rankings, k=0)[0] == "z"
    # k=60 (défaut) : les écarts de rang s'écrasent, la présence dans plusieurs
    # listes l'emporte (1/63+1/63≈0.032 pour w, contre 1/61≈0.016 pour z).
    assert reciprocal_rank_fusion(rankings, k=60)[0] == "w"
