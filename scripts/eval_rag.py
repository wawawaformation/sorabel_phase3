"""Mesure du gain hybride/rerank sur eval/questions_rag.jsonl (E6) et calibration
du seuil de refus (E1).

    uv run python scripts/eval_rag.py            # rapport dans eval/rapport_gain.md
"""

import json
from dataclasses import dataclass
from pathlib import Path

from gateway.chroma import chroma_client, open_collection
from gateway.embedder import AzureEmbedder
from gateway.settings import get_settings
from retrieval.engine import SearchEngine
from retrieval.reranker import AzureCohereReranker

EVAL_FILE = Path("eval/questions_rag.jsonl")
REPORT_FILE = Path("eval/rapport_gain.md")
SEUILS = [0.10, 0.20, 0.30, 0.40, 0.50, 0.55, 0.60, 0.62, 0.64, 0.65, 0.66, 0.68, 0.70]


@dataclass
class Config:
    label: str
    dense_only: bool
    rerank: bool


CONFIGS = [
    Config("A — dense seul", dense_only=True, rerank=False),
    Config("B — hybride (Dense+BM25+RRF)", dense_only=False, rerank=False),
    Config("C — hybride + rerank", dense_only=False, rerank=True),
]


def load_questions() -> list[dict]:
    return [json.loads(line) for line in EVAL_FILE.read_text("utf-8").splitlines() if line.strip()]


def build_engine(config: Config, settings, collection, embedder):
    """Moteur pour une configuration donnée.

    refusal_threshold=0.0 : on mesure ici la qualité du classement, le seuil de refus
    est calibré séparément à partir de la distribution des scores.
    """
    updates: dict = {"rerank_enabled": config.rerank, "refusal_threshold": 0.0}
    if config.dense_only:
        updates["lexical_candidates"] = 0  # neutralise la piste BM25
    tuned = settings.model_copy(update=updates)
    reranker = AzureCohereReranker(tuned) if config.rerank else None
    return SearchEngine(collection, embedder, tuned, reranker=reranker)


def hit_ok(question: dict, hits) -> bool:
    if "attendu_reference" in question:
        return any(h.chunk.ref_produit == question["attendu_reference"] for h in hits)
    if "attendu_type" in question:
        return any(h.chunk.type_doc == question["attendu_type"] for h in hits)
    return False


def main() -> None:
    settings = get_settings()
    collection = open_collection(chroma_client(settings), settings.chroma_collection)
    embedder = AzureEmbedder(settings)
    questions = load_questions()
    couvertes = [q for q in questions if q["type"] == "couverte"]
    references = [q for q in questions if q["type"] == "reference_exacte"]
    hors = [q for q in questions if q["type"] == "hors_corpus"]

    lines = ["# Rapport de gain — recherche avancée vs recherche simple (E6)", ""]
    lines += [f"Jeu d'évaluation : `{EVAL_FILE}` — {len(questions)} questions "
              f"({len(couvertes)} couvertes, {len(references)} par référence exacte, "
              f"{len(hors)} hors corpus).", ""]
    lines += ["| Configuration | Couvertes top-1 | Couvertes top-5 | Références exactes |",
              "|---|---|---|---|"]

    scores_hors: list[float] = []
    scores_couvertes: list[float] = []
    for config in CONFIGS:
        engine = build_engine(config, settings, collection, embedder)
        top1 = sum(hit_ok(q, engine.search(q["question"], top_k=1).hits) for q in couvertes)
        top5 = sum(hit_ok(q, engine.search(q["question"], top_k=5).hits) for q in couvertes)
        refs = sum(hit_ok(q, engine.search(q["question"]).hits) for q in references)
        lines.append(f"| {config.label} | {top1}/{len(couvertes)} | {top5}/{len(couvertes)} "
                     f"| {refs}/{len(references)} |")
        if config.rerank:
            for q in hors:
                out = engine.search(q["question"])
                scores_hors.append(max((h.rerank_score or 0.0) for h in out.hits) if out.hits else 0.0)
            for q in couvertes:
                out = engine.search(q["question"])
                scores_couvertes.append(max((h.rerank_score or 0.0) for h in out.hits) if out.hits else 0.0)

    lines += ["", "## Calibration du seuil de refus (E1)", "",
              "Le seuil porte sur le score du reranker, jamais sur un score de fusion "
              "(le score RRF classe un hors-corpus plus haut qu'une question couverte).", "",
              "| Seuil | Hors corpus refusés | Couvertes refusées à tort |", "|---|---|---|"]
    for seuil in SEUILS:
        refuses = sum(1 for s in scores_hors if s < seuil)
        faux = sum(1 for s in scores_couvertes if s < seuil)
        lines.append(f"| {seuil:.2f} | {refuses}/{len(scores_hors)} | {faux}/{len(scores_couvertes)} |")

    parfaits = [s for s in SEUILS
                if all(x < s for x in scores_hors) and all(x >= s for x in scores_couvertes)]
    if parfaits:
        lines += ["", f"**Seuil retenu : {parfaits[len(parfaits) // 2]:.2f}** — refuse tous les "
                      "hors-corpus sans refuser aucune question couverte."]
    else:
        lines += ["", "**Aucun seuil ne sépare parfaitement les deux populations sur ce jeu.** "
                      "Le compromis retenu est documenté ici plutôt que masqué : voir le tableau "
                      "ci-dessus pour choisir entre rappel et précision du refus."]

    lines += ["", "## Scores bruts (hors corpus / couvertes, configuration C)", ""]
    lines += ["Hors corpus : " + ", ".join(f"{s:.3f}" for s in sorted(scores_hors))]
    lines += ["", "Couvertes : " + ", ".join(f"{s:.3f}" for s in sorted(scores_couvertes))]

    REPORT_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"rapport écrit dans {REPORT_FILE}")


if __name__ == "__main__":
    main()
