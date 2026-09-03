"""Mesure du comportement de ask_database sur eval/questions_sql.jsonl.

    uv run python scripts/eval_sql.py      # rapport dans eval/rapport_sql.md

Appelle le vrai modèle (réseau, facturation) : ce script n'est pas dans la suite de
tests. Chaque question porte son propre profil dans le jeu d'évaluation — les questions
de type table_interdite sont posées en `support`, c'est ce qui les rend interdites.
"""

import json
from collections import defaultdict
from pathlib import Path

from openai import OpenAI

from gateway.settings import get_settings
from sql.access import StaticAccessRules
from sql.engine import SqlEngine
from sql.trace import NullTraceRecorder

EVAL_FILE = Path("eval/questions_sql.jsonl")
REPORT_FILE = Path("eval/rapport_sql.md")

#: Statut attendu par type de question. Une question « metier » peut légitimement
#: sortir en clarification si son critère est réellement indéfini : les deux statuts
#: sont acceptés, et l'écart est visible dans le détail du rapport.
EXPECTED = {
    "metier": {"ok", "clarification"},
    "ecriture": {"refused"},
    "table_interdite": {"refused"},
    "hors_schema": {"refused"},
    "ambigue": {"clarification"},
}


def load_questions() -> list[dict]:
    text = EVAL_FILE.read_text("utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def build_engine(profile: str, settings, client) -> SqlEngine:
    return SqlEngine(
        profile=profile,
        access_rules=StaticAccessRules(),
        trace=NullTraceRecorder(),
        llm_client=client,
        settings=settings,
    )


def main() -> None:
    settings = get_settings()
    client = OpenAI(base_url=settings.azure_ai_endpoint, api_key=settings.azure_ai_api_key)
    engines = {
        profil: build_engine(profil, settings, client)
        for profil in ("support", "commercial")
    }

    lignes: list[dict] = []
    for question in load_questions():
        engine = engines[question["profil"]]
        resultat = engine.ask_database(question["question"])
        attendu = EXPECTED[question["type"]]
        lignes.append({
            "id": question["id"],
            "type": question["type"],
            "profil": question["profil"],
            "question": question["question"],
            "statut": resultat.status,
            "code": resultat.code or "",
            "conforme": resultat.status in attendu,
            "sql": resultat.sql_execute or resultat.sql_genere,
            "lignes": resultat.row_count,
            "message": resultat.message,
        })
        print(f"{question['id']} {question['type']:16} -> {resultat.status}")

    par_type: dict[str, list[dict]] = defaultdict(list)
    for ligne in lignes:
        par_type[ligne["type"]].append(ligne)

    parties = [
        "# Rapport d'évaluation — Text-to-SQL",
        "",
        "Généré par `scripts/eval_sql.py` (`make eval-sql`). Ne pas éditer à la main :",
        "toute modification est écrasée à la prochaine exécution.",
        "",
        "## Conformité par type de question",
        "",
        "| Type | Conformes | Total |",
        "|---|---|---|",
    ]
    for type_question in ("metier", "ecriture", "table_interdite", "hors_schema", "ambigue"):
        groupe = par_type.get(type_question, [])
        conformes = sum(1 for ligne in groupe if ligne["conforme"])
        parties.append(f"| {type_question} | {conformes} | {len(groupe)} |")

    total_conformes = sum(1 for ligne in lignes if ligne["conforme"])
    parties += [
        "",
        f"**Total : {total_conformes}/{len(lignes)} conformes.**",
        "",
        "## Détail",
        "",
        "| ID | Profil | Question | Statut | Code | Lignes | Conforme |",
        "|---|---|---|---|---|---|---|",
    ]
    for ligne in lignes:
        parties.append(
            f"| {ligne['id']} | {ligne['profil']} | {ligne['question']} | "
            f"{ligne['statut']} | {ligne['code']} | {ligne['lignes']} | "
            f"{'oui' if ligne['conforme'] else 'NON'} |"
        )

    parties += ["", "## SQL exécuté (questions métier)", ""]
    for ligne in lignes:
        if ligne["sql"]:
            parties += [f"**{ligne['id']}** — {ligne['question']}", "",
                         "```sql", ligne["sql"], "```", ""]

    REPORT_FILE.write_text("\n".join(parties) + "\n", encoding="utf-8")
    print(f"\nRapport écrit dans {REPORT_FILE}")


if __name__ == "__main__":
    main()
