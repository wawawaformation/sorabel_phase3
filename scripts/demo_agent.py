"""Agent de démonstration du RAG hybride avec reranking.

    uv run python scripts/demo_agent.py "que faire si un colis arrive endommagé ?"
    uv run python scripts/demo_agent.py --no-rerank "…"     # montre l'apport du rerank
    uv run python scripts/demo_agent.py --show-stages "…"   # détaille le pipeline
    uv run python scripts/demo_agent.py                     # boucle interactive
"""

import argparse

from openai import OpenAI

from gateway.chroma import chroma_client, open_collection
from gateway.embedder import AzureEmbedder
from gateway.settings import get_settings
from retrieval.answer import compose_answer, format_citation
from retrieval.engine import SearchEngine, SearchOutcome
from retrieval.reranker import AzureCohereReranker

STAGE_LABELS = {
    "reference": "0. Routing par référence exacte — retrieval/routing.py",
    "dense": "1. Dense (distance L2, plus bas = plus proche) — retrieval/dense.py",
    "lexical": "2. BM25 (score, plus haut = meilleur) — retrieval/lexical.py",
    "fused": "3. Fusion RRF (score, plus haut = meilleur) — retrieval/fusion.py",
    "versioned": "4. Dernière version par famille (anti-doublons) — retrieval/dedup.py",
    "diversified": "5. Diversification par thème (anti-quasi-doublons) — retrieval/dedup.py",
    "reranked": "6. Rerank Cohere — retrieval/reranker.py",
}


def show_stages(outcome: SearchOutcome) -> None:
    print("\n--- Étapes du pipeline ---")
    for key, label in STAGE_LABELS.items():
        if key in outcome.stages:
            ids = outcome.stages[key]
            scores = outcome.stage_scores.get(key)
            print(f"{label} — {len(ids)} candidats :")
            for chunk_id in ids[:3]:
                suffix = f"  ({scores[chunk_id]:.4f})" if scores else ""
                print(f"    {chunk_id}{suffix}")


def render(outcome: SearchOutcome, answer: str | None) -> None:
    print(f"\nRoute : {outcome.route}")
    if outcome.is_refusal:
        print(f"\n❌ REFUS — {outcome.reason}")
        return
    if not outcome.hits:
        print(f"\n(aucun résultat — {outcome.reason})")
        return
    print("\n--- Passages retenus ---")
    for position, hit in enumerate(outcome.hits, start=1):
        score = f"{hit.rerank_score:.4f}" if hit.rerank_score is not None else "  —   "
        print(f"[{position}] score={score}  {format_citation(hit.chunk)}")
    if answer:
        print(f"\n--- Réponse ---\n{answer}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Démo du RAG hybride Sorabel")
    parser.add_argument("question", nargs="?", help="question ; absente = mode interactif")
    parser.add_argument("--no-rerank", action="store_true", help="désactive le reranking")
    parser.add_argument("--show-stages", action="store_true", help="détaille le pipeline")
    parser.add_argument("--no-answer", action="store_true", help="passages seuls, sans LLM")
    args = parser.parse_args()

    settings = get_settings()
    if args.no_rerank:
        settings = settings.model_copy(update={"rerank_enabled": False})
    collection = open_collection(chroma_client(settings), settings.chroma_collection)
    reranker = None if args.no_rerank else AzureCohereReranker(settings)
    engine = SearchEngine(collection, AzureEmbedder(settings), settings, reranker=reranker)
    llm = OpenAI(base_url=settings.azure_ai_endpoint, api_key=settings.azure_ai_api_key)

    def handle(question: str) -> None:
        outcome = engine.search(question)
        if args.show_stages:
            show_stages(outcome)
        answer = None
        if outcome.hits and not args.no_answer:
            answer = compose_answer(
                llm, settings.azure_model_text_generation, question, outcome.hits
            )
        render(outcome, answer)

    if args.question:
        handle(args.question)
        return
    print("Mode interactif — Ctrl-D pour quitter.")
    while True:
        try:
            question = input("\nQuestion > ").strip()
        except EOFError:
            print()
            return
        if question:
            handle(question)


if __name__ == "__main__":
    main()
