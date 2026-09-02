from retrieval.answer import ANSWER_SYSTEM_PROMPT, build_context, compose_answer, format_citation
from retrieval.corpus import IndexedChunk
from retrieval.engine import Hit


def _hit(title: str, ref: str | None) -> Hit:
    return Hit(
        chunk=IndexedChunk(
            chunk_id="c#0", document_id="c", content="Le délai est de 5 jours ouvrés.",
            title=title, type_doc="procedure_sav", collection="sav", version="2.0",
            date="2026-04-05", source="html", family_id="f",
            diversification_group="g", ref_produit=ref,
        ),
        rerank_score=0.85,
    )


class FakeLLM:
    """Double du client OpenAI : capture les messages, renvoie une réponse fixe."""

    def __init__(self) -> None:
        self.captured: dict = {}

        class Completions:
            def create(inner, **kwargs):  # noqa: N805
                self.captured = kwargs

                class Msg:
                    content = "Le délai est de 5 jours ouvrés (Colis endommagé, 2026-04-05)."

                class Choice:
                    message = Msg()

                class Response:
                    choices = [Choice()]

                return Response()

        class Chat:
            completions = Completions()

        self.chat = Chat()


def test_citation_contient_titre_date_et_reference():
    assert format_citation(_hit("Colis endommagé", "REF-8842").chunk) == (
        "Colis endommagé — REF-8842 — 2026-04-05"
    )


def test_citation_sans_reference():
    # sav/ et notes/ n'ont pas de ref_produit : la citation reste valide (E1).
    assert format_citation(_hit("Colis endommagé", None).chunk) == (
        "Colis endommagé — 2026-04-05"
    )


def test_contexte_numerote_les_passages():
    ctx = build_context([_hit("A", None), _hit("B", "REF-1")])
    assert "[1] A — 2026-04-05" in ctx
    assert "[2] B — REF-1 — 2026-04-05" in ctx


def test_prompt_interdit_de_repondre_hors_passages():
    assert "uniquement" in ANSWER_SYSTEM_PROMPT.lower()


def test_compose_answer_utilise_max_completion_tokens():
    llm = FakeLLM()
    out = compose_answer(llm, "gpt-5.4-mini", "quel délai ?", [_hit("Colis endommagé", None)])
    assert "5 jours" in out
    # gpt-5.4-mini refuse max_tokens (spec § 2.4).
    assert "max_completion_tokens" in llm.captured
    assert "max_tokens" not in llm.captured
    assert llm.captured["model"] == "gpt-5.4-mini"
