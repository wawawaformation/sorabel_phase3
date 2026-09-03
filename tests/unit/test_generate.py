import json

import pytest

from sql.generate import Generation, generate_sql, looks_like_write


class FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = FakeMessage(content)


class FakeCompletion:
    def __init__(self, content: str) -> None:
        self.choices = [FakeChoice(content)]


class FakeCompletions:
    """Enregistre les arguments reçus, pour vérifier la forme de l'appel."""

    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.last_kwargs: dict = {}

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        return FakeCompletion(json.dumps(self._payload))


class FakeChat:
    def __init__(self, completions: FakeCompletions) -> None:
        self.completions = completions


class FakeClient:
    def __init__(self, payload: dict) -> None:
        self.completions = FakeCompletions(payload)
        self.chat = FakeChat(self.completions)


def _payload(**overrides) -> dict:
    base = {
        "status": "SQL_GENERABLE",
        "sql": "SELECT SUM(quantite) FROM stocks WHERE ref = 'REF-8842'",
        "tables_referencees": ["stocks"],
        "colonnes_referencees": ["stocks.ref", "stocks.quantite"],
        "clarification": "",
        "reason": "",
    }
    base.update(overrides)
    return base


@pytest.mark.parametrize(
    "question",
    [
        "supprime les commandes de test",
        "mets à jour le prix de la REF-8842 à 89,90",
        "insère un client de démonstration",
        "vide la table ventes",
        "DROP TABLE ventes",
    ],
)
def test_intention_d_ecriture_detectee_avant_le_llm(question):
    # Détection en amont pour que la trace distingue une tentative d'écriture d'une
    # simple question hors périmètre (spec § 4.11).
    assert looks_like_write(question) is True


@pytest.mark.parametrize(
    "question",
    [
        "combien de commandes en avril ?",
        "quel est le stock total de la REF-8842 ?",
        "quelle est la météo à Lille demain ?",
        "les 5 produits les plus vendus en quantité",
    ],
)
def test_question_de_lecture_non_detectee_comme_ecriture(question):
    assert looks_like_write(question) is False


def test_generation_decodee_dans_un_objet_type():
    client = FakeClient(_payload())
    resultat = generate_sql(client, "gpt-5.4-mini", "stock de REF-8842 ?", "TABLE stocks…")
    assert isinstance(resultat, Generation)
    assert resultat.status == "SQL_GENERABLE"
    assert resultat.tables == ("stocks",)
    assert resultat.columns == ("stocks.ref", "stocks.quantite")


def test_appel_structure_strict_et_max_completion_tokens():
    # gpt-5.4-mini refuse max_tokens (vérifié) ; le json_schema strict garantit que
    # la réponse est décodable sans parsing défensif (spec § 2.11).
    client = FakeClient(_payload())
    generate_sql(client, "gpt-5.4-mini", "stock ?", "TABLE stocks…")
    kwargs = client.completions.last_kwargs
    assert "max_completion_tokens" in kwargs
    assert "max_tokens" not in kwargs
    assert kwargs["response_format"]["type"] == "json_schema"
    assert kwargs["response_format"]["json_schema"]["strict"] is True


def test_schema_filtre_transmis_dans_le_prompt_systeme():
    client = FakeClient(_payload())
    generate_sql(client, "gpt-5.4-mini", "stock ?", "TABLE stocks\n  quantite (INTEGER)")
    systeme = client.completions.last_kwargs["messages"][0]["content"]
    assert "TABLE stocks" in systeme
    assert "quantite (INTEGER)" in systeme


def test_statut_ambigu_sans_sql():
    client = FakeClient(_payload(
        status="AMBIGUOUS", sql="", tables_referencees=[], colonnes_referencees=[],
        clarification="Quel critère définit le meilleur client ?",
    ))
    resultat = generate_sql(client, "gpt-5.4-mini", "le meilleur client ?", "…")
    assert resultat.status == "AMBIGUOUS"
    assert resultat.sql == ""
    assert resultat.clarification.startswith("Quel critère")


def test_statut_hors_schema_sans_sql():
    client = FakeClient(_payload(
        status="OUT_OF_SCHEMA", sql="", tables_referencees=[], colonnes_referencees=[],
        reason="La question porte sur la météo.",
    ))
    resultat = generate_sql(client, "gpt-5.4-mini", "météo ?", "…")
    assert resultat.status == "OUT_OF_SCHEMA"
    assert resultat.sql == ""


def test_reponse_vide_du_modele_traitee_comme_hors_schema():
    # Robustesse : plutôt qu'une exception qui remonterait brute jusqu'au client.
    client = FakeClient({})
    resultat = generate_sql(client, "gpt-5.4-mini", "stock ?", "…")
    assert resultat.status == "OUT_OF_SCHEMA"
