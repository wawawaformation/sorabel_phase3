"""Journalisation des appels (E5) — interface injectée, implémentation locale.

La conception impose un **journal unique** pour tout le serveur MCP, partagé avec le
RAG : « pas un journal par chantier ». Ce journal appartient donc au chantier MCP, qui
n'existe pas encore. D'où le ``Protocol`` : ``SqlEngine`` écrit à travers cette
interface, et le Chantier 3 injectera le journal réel sans modifier ``sql/``.

``JsonlTraceRecorder`` duplique en plus les seules entrées ``code="FORBIDDEN"`` (les
tentatives d'écriture) dans un seul fichier dédié, pensé pour une surveillance directe
par ``tail -f`` sans avoir à filtrer le journal général. Cette duplication n'est jamais
une source primaire : en cas de divergence, le journal unique fait foi (spec § 4.11).
"""

import json
from pathlib import Path
from typing import Protocol

#: Code de journal des tentatives d'écriture, seul dupliqué dans le fichier d'alerte.
FORBIDDEN = "FORBIDDEN"


class TraceRecorder(Protocol):
    """Contrat minimal : enregistrer une entrée de journal déjà constituée.

    Volontairement un dict et non un type structuré : l'enveloppe exacte du journal
    est définie par la conception MCP (`journal_mcp.md`) et sera figée au Chantier 3.
    Contraindre sa forme ici obligerait à la redéfinir deux fois.
    """

    def record(self, entry: dict[str, object]) -> None: ...


class JsonlTraceRecorder:
    """Écriture JSONL append-only : le journal unique, plus la vue filtrée des alertes.

    Le format JSONL est retenu plutôt qu'une table SQL parce qu'une trace est
    naturellement append-only et se lit directement (``cat``, ``tail -f``) — et parce
    qu'écrire dans ``sorabel.db`` contredirait la lecture seule du fichier métier
    (conception § 2.8).
    """

    def __init__(self, journal_path: Path, alert_path: Path) -> None:
        self._journal_path = journal_path
        self._alert_path = alert_path

    def record(self, entry: dict[str, object]) -> None:
        self._append(self._journal_path, entry)
        if entry.get("code") == FORBIDDEN:
            self._append(self._alert_path, entry)

    @staticmethod
    def _append(path: Path, entry: dict[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        # ensure_ascii=False : le journal doit rester lisible à l'œil, accents compris.
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


class NullTraceRecorder:
    """N'enregistre rien. Pour les tests qui n'ont rien à vérifier sur la trace."""

    def record(self, entry: dict[str, object]) -> None:
        return None
