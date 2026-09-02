# Rapport de gain — recherche avancée vs recherche simple (E6)

Jeu d'évaluation : `eval/questions_rag.jsonl` — 30 questions (14 couvertes, 8 par référence exacte, 8 hors corpus).

| Configuration | Couvertes top-1 | Couvertes top-5 | Références exactes |
|---|---|---|---|
| A — dense seul | 11/14 | 13/14 | 8/8 |
| B — hybride (Dense+BM25+RRF) | 12/14 | 12/14 | 8/8 |
| C — hybride + rerank | 12/14 | 13/14 | 8/8 |

## Calibration du seuil de refus (E1)

Le seuil porte sur le score du reranker, jamais sur un score de fusion (le score RRF classe un hors-corpus plus haut qu'une question couverte).

| Seuil | Hors corpus refusés | Couvertes refusées à tort |
|---|---|---|
| 0.10 | 0/8 | 0/14 |
| 0.20 | 0/8 | 0/14 |
| 0.30 | 0/8 | 0/14 |
| 0.40 | 4/8 | 0/14 |
| 0.50 | 6/8 | 0/14 |
| 0.55 | 7/8 | 0/14 |
| 0.60 | 7/8 | 0/14 |
| 0.62 | 7/8 | 0/14 |
| 0.64 | 8/8 | 0/14 |
| 0.65 | 8/8 | 0/14 |
| 0.66 | 8/8 | 0/14 |
| 0.68 | 8/8 | 1/14 |
| 0.70 | 8/8 | 1/14 |

**Seuil retenu : 0.65** — la grille initiale (pas de 0,10) était trop grossière pour le voir, mais un
seuil parfait existe bel et bien : max(hors corpus) = 0,626, min(couvertes) = 0,669. 0,65 refuse les
8/8 hors-corpus sans refuser aucune des 14 couvertes, avec une marge symétrique (~0,02) des deux côtés.
La valeur provisoire de 0,40 posée avant calibration était trop basse : elle ne refusait que 4/8
hors-corpus — vérifié en démo (question télétravail à 0,4164, juste au-dessus de 0,40, donc non
refusée par le moteur bien que le LLM ait quand même refusé de répondre en aval).

## Scores bruts (hors corpus / couvertes, configuration C)

Hors corpus : 0.309, 0.339, 0.368, 0.394, 0.413, 0.416, 0.517, 0.626

Couvertes : 0.669, 0.788, 0.862, 0.864, 0.866, 0.888, 0.890, 0.919, 0.924, 0.934, 0.946, 0.947, 0.963, 0.964
