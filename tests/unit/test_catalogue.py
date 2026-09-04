def test_les_8_tools_du_brief_sont_presents():
    from mcp_server.catalogue import TOOL_NAMES

    assert set(TOOL_NAMES) == {
        "answer_question", "search_docs", "get_document", "list_sources",
        "ask_database", "get_schema", "check_stock", "order_status",
    }


def test_chaque_tool_a_une_description_et_un_schema_objet():
    from mcp_server.access import DEFAULT_MATRIX_PATH, YamlAccessRules, load_matrix
    from mcp_server.catalogue import build_tools

    tools = build_tools(YamlAccessRules(load_matrix(DEFAULT_MATRIX_PATH)))
    assert len(tools) == 8
    for tool in tools:
        assert tool.description and tool.description.strip()
        assert tool.inputSchema["type"] == "object"


def test_les_entrees_obligatoires_du_brief_sont_requises():
    from mcp_server.access import DEFAULT_MATRIX_PATH, YamlAccessRules, load_matrix
    from mcp_server.catalogue import build_tools

    tools = {t.name: t for t in build_tools(YamlAccessRules(load_matrix(DEFAULT_MATRIX_PATH)))}
    assert tools["ask_database"].inputSchema["required"] == ["question"]
    assert tools["check_stock"].inputSchema["required"] == ["ref"]
    assert tools["order_status"].inputSchema["required"] == ["order_id"]
    assert tools["get_document"].inputSchema["required"] == ["doc_id"]
    assert "required" not in tools["get_schema"].inputSchema
    assert "required" not in tools["list_sources"].inputSchema


def test_meta_roles_genere_depuis_la_matrice_reelle():
    # Aujourd'hui : les deux profils ont les mêmes tools (spec_mcp.md § 2, point 1/4).
    from mcp_server.access import DEFAULT_MATRIX_PATH, YamlAccessRules, load_matrix
    from mcp_server.catalogue import build_tools

    tools = build_tools(YamlAccessRules(load_matrix(DEFAULT_MATRIX_PATH)))
    for tool in tools:
        assert tool.meta == {"sorabel/roles": ["commercial", "support"]}


def test_meta_roles_reflete_une_restriction_de_tool_si_elle_existe():
    # Vérifie que build_tools lit vraiment la matrice (pas une valeur figée) : avec une
    # matrice où un tool est réservé à un profil, _meta le reflète.
    from mcp_server.access import ProfileRules, YamlAccessRules
    from mcp_server.catalogue import build_tools

    matrix = {
        "support": ProfileRules(tools=frozenset({"check_stock"}), rag_collections=frozenset(),
                                 hidden_columns=frozenset()),
        "commercial": ProfileRules(tools=frozenset({"check_stock", "get_schema"}),
                                    rag_collections=frozenset(), hidden_columns=frozenset()),
    }
    tools = {t.name: t for t in build_tools(YamlAccessRules(matrix))}
    assert tools["get_schema"].meta == {"sorabel/roles": ["commercial"]}
    assert tools["check_stock"].meta == {"sorabel/roles": ["commercial", "support"]}
