from retrieval.corpus import IndexedChunk
from retrieval.lexical import LexicalIndex


def _chunk(chunk_id: str, content: str) -> IndexedChunk:
    return IndexedChunk(
        chunk_id=chunk_id, document_id=chunk_id.split("#")[0], content=content,
        title="T", type_doc="fiche_technique", collection="fiches", version="1.0",
        date="2024-01-01", source="pdf", family_id="fam", diversification_group="grp",
        ref_produit=None,
    )


def _index() -> LexicalIndex:
    return LexicalIndex([
        _chunk("colis#0", "Procédure SAV : colis reçu endommagé, constat et photos."),
        _chunk("led#0", "Notice d'installation projecteur LED rechargeable."),
        _chunk("disj#0", "Fiche technique disjoncteur triphasé 63 A courbe D."),
    ])


def test_match_lexical_classe_en_tete():
    assert _index().search("colis endommagé", limit=3)[0] == "colis#0"


def test_accents_indifferents():
    # « triphase » sans accent doit retrouver « triphasé » (tokenisation repliée).
    assert _index().search("disjoncteur triphase", limit=1) == ["disj#0"]


def test_limite_respectee():
    assert len(_index().search("colis", limit=2)) == 2


def test_index_vide_ne_plante_pas():
    assert LexicalIndex([]).search("colis", limit=5) == []


def test_avec_scores_plus_haut_est_meilleur():
    results = _index().search_with_scores("colis endommagé", limit=3)
    ids = [chunk_id for chunk_id, _ in results]
    assert ids[0] == "colis#0"
    scores = [score for _, score in results]
    assert scores == sorted(scores, reverse=True)
    assert scores[0] > 0.0  # au moins un mot en commun


def test_search_reste_coherent_avec_la_version_scoree():
    plain = _index().search("colis", limit=3)
    scored = _index().search_with_scores("colis", limit=3)
    assert plain == [chunk_id for chunk_id, _ in scored]
