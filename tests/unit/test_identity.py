import pytest


def test_profil_par_defaut_si_variable_absente(monkeypatch):
    from mcp_server.identity import EnvVarIdentityResolver

    monkeypatch.delenv("SORABEL_PROFILE", raising=False)
    assert EnvVarIdentityResolver().resolve() == "support"


def test_lit_le_profil_depuis_la_variable(monkeypatch):
    from mcp_server.identity import EnvVarIdentityResolver

    monkeypatch.setenv("SORABEL_PROFILE", "commercial")
    assert EnvVarIdentityResolver().resolve() == "commercial"


def test_profil_invalide_leve_value_error(monkeypatch):
    from mcp_server.identity import EnvVarIdentityResolver

    monkeypatch.setenv("SORABEL_PROFILE", "admin")
    with pytest.raises(ValueError, match="profil inconnu"):
        EnvVarIdentityResolver().resolve()
