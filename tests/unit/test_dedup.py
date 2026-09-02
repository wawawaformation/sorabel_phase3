from retrieval.corpus import IndexedChunk
from retrieval.dedup import diversify, keep_latest_version, version_key


def _chunk(chunk_id: str, family: str, version: str, group: str) -> IndexedChunk:
    return IndexedChunk(
        chunk_id=chunk_id, document_id=chunk_id.split("#")[0], content="c", title="T",
        type_doc="procedure_sav", collection="sav", version=version, date="2024-01-01",
        source="html", family_id=family, diversification_group=group, ref_produit=None,
    )


BY_ID = {
    c.chunk_id: c
    for c in [
        _chunk("proc-01-v1.0#0", "proc-01", "1.0", "sav_casse"),
        _chunk("proc-01-v2.0#0", "proc-01", "2.0", "sav_casse"),
        _chunk("proc-02-v1.0#0", "proc-02", "1.0", "sav_casse"),
        _chunk("notice-v1.1#0", "notice", "1.1", "notice_x"),
    ]
}


def test_version_key_compare_numeriquement():
    assert version_key("2.1") == (2, 1)
    assert version_key("1.10") > version_key("1.9")  # 10 > 9, pas un tri de chaînes


def test_garde_la_derniere_version_de_chaque_famille():
    # v1.0 est mieux classée, mais c'est la v2.0 qui doit sortir : la conception
    # retient « dernière version par défaut ».
    got = keep_latest_version(["proc-01-v1.0#0", "proc-01-v2.0#0", "notice-v1.1#0"], BY_ID)
    assert got == ["proc-01-v2.0#0", "notice-v1.1#0"]


def test_position_de_la_famille_preservee():
    # La famille garde le meilleur rang qu'elle occupait.
    got = keep_latest_version(["notice-v1.1#0", "proc-01-v1.0#0", "proc-01-v2.0#0"], BY_ID)
    assert got == ["notice-v1.1#0", "proc-01-v2.0#0"]


def test_diversification_un_seul_par_groupe():
    got = diversify(["proc-01-v2.0#0", "proc-02-v1.0#0", "notice-v1.1#0"], BY_ID)
    assert got == ["proc-01-v2.0#0", "notice-v1.1#0"]


def test_listes_vides():
    assert keep_latest_version([], BY_ID) == []
    assert diversify([], BY_ID) == []
