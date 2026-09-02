"""Rédaction de la réponse sourcée.

La génération est côté client, hors du retrieval : le moteur retourne des passages,
l'agent compose (conception « LLM côté client, hors MCP »).
"""

from typing import Any

from retrieval.corpus import IndexedChunk
from retrieval.engine import Hit

MAX_ANSWER_TOKENS = 700

ANSWER_SYSTEM_PROMPT = """\
Tu réponds à des questions internes de Sorabel, distributeur de matériel électrique.

Règles impératives :
- Réponds UNIQUEMENT à partir des passages fournis. N'ajoute aucune connaissance externe.
- Cite tes sources en fin de phrase, sous la forme (titre — référence — date).
- Si les passages ne permettent pas de répondre, dis-le explicitement au lieu d'inventer.
- Réponds en français, de façon concise et opérationnelle.\
"""


def format_citation(chunk: IndexedChunk) -> str:
    parts = [chunk.title]
    if chunk.ref_produit:
        parts.append(chunk.ref_produit)
    parts.append(chunk.date)
    return " — ".join(parts)


def build_context(hits: list[Hit]) -> str:
    blocks = []
    for position, hit in enumerate(hits, start=1):
        blocks.append(f"[{position}] {format_citation(hit.chunk)}\n{hit.chunk.content}")
    return "\n\n".join(blocks)


def compose_answer(client: Any, model: str, question: str, hits: list[Hit]) -> str:
    """Appelle le LLM avec les passages retenus. `client` a la forme du SDK openai."""
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": ANSWER_SYSTEM_PROMPT},
            {"role": "user", "content": f"Question : {question}\n\nPassages :\n{build_context(hits)}"},
        ],
        max_completion_tokens=MAX_ANSWER_TOKENS,  # gpt-5.4-mini refuse max_tokens
    )
    return str(response.choices[0].message.content or "").strip()
