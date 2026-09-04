import pytest

from sql.access import SENSITIVE_COLUMNS


def test_la_matrice_reelle_se_charge_sans_erreur():
    from mcp_server.access import DEFAULT_MATRIX_PATH, load_matrix

    matrix = load_matrix(DEFAULT_MATRIX_PATH)
    assert set(matrix) == {"support", "commercial"}


def test_colonnes_cachees_support_coherentes_avec_sql_access():
    from mcp_server.access import DEFAULT_MATRIX_PATH, YamlAccessRules, load_matrix

    rules = YamlAccessRules(load_matrix(DEFAULT_MATRIX_PATH))
    assert rules.hidden_columns("support") == SENSITIVE_COLUMNS


def test_commercial_sans_colonne_cachee():
    from mcp_server.access import DEFAULT_MATRIX_PATH, YamlAccessRules, load_matrix

    rules = YamlAccessRules(load_matrix(DEFAULT_MATRIX_PATH))
    assert rules.hidden_columns("commercial") == frozenset()


def test_get_schema_accessible_aux_deux_profils():
    # Décision de cette session : aucun tool n'est interdit dans son intégralité
    # (spec_mcp.md § 2, point 1 / § 4.3) — contrairement à la matrice de
    # docs/cadrage_dsi.md, écartée.
    from mcp_server.access import DEFAULT_MATRIX_PATH, YamlAccessRules, load_matrix

    rules = YamlAccessRules(load_matrix(DEFAULT_MATRIX_PATH))
    assert "get_schema" in rules.allowed_tools("support")
    assert "get_schema" in rules.allowed_tools("commercial")


def test_profil_inconnu_leve_value_error():
    from mcp_server.access import DEFAULT_MATRIX_PATH, YamlAccessRules, load_matrix

    rules = YamlAccessRules(load_matrix(DEFAULT_MATRIX_PATH))
    with pytest.raises(ValueError, match="profil inconnu"):
        rules.hidden_columns("admin")


def test_load_matrix_rejette_un_profil_manquant(tmp_path):
    from mcp_server.access import load_matrix

    incomplet = tmp_path / "incomplet.yaml"
    incomplet.write_text(
        "profiles:\n  support:\n    tools: []\n    rag_collections: []\n"
        "    sql_hidden_columns: []\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="profils"):
        load_matrix(incomplet)


def test_load_matrix_rejette_un_profil_inconnu(tmp_path):
    from mcp_server.access import load_matrix

    en_trop = tmp_path / "en_trop.yaml"
    en_trop.write_text(
        "profiles:\n"
        "  support: {tools: [], rag_collections: [], sql_hidden_columns: []}\n"
        "  commercial: {tools: [], rag_collections: [], sql_hidden_columns: []}\n"
        "  admin: {tools: [], rag_collections: [], sql_hidden_columns: []}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="profils"):
        load_matrix(en_trop)
