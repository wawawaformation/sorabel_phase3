import json
from pathlib import Path

from sql.trace import JsonlTraceRecorder, NullTraceRecorder


def _lignes(chemin: Path) -> list[dict]:
    if not chemin.exists():
        return []
    return [json.loads(x) for x in chemin.read_text("utf-8").splitlines() if x.strip()]


def test_toute_entree_va_dans_le_journal_unique(tmp_path):
    journal, alerte = tmp_path / "audit.jsonl", tmp_path / "alerte.jsonl"
    recorder = JsonlTraceRecorder(journal, alerte)
    recorder.record({"tool": "ask_database", "statut": "ok", "code": None})
    recorder.record({"tool": "ask_database", "statut": "refused", "code": "OUT_OF_SCHEMA"})
    assert len(_lignes(journal)) == 2


def test_seules_les_tentatives_d_ecriture_sont_dupliquees(tmp_path):
    # Le second fichier est une vue filtrée pour surveillance directe, jamais un
    # journal parallèle : le journal unique reste la source de vérité (spec § 4.11).
    journal, alerte = tmp_path / "audit.jsonl", tmp_path / "alerte.jsonl"
    recorder = JsonlTraceRecorder(journal, alerte)
    recorder.record({"tool": "ask_database", "statut": "ok", "code": None})
    recorder.record({"tool": "ask_database", "statut": "refused", "code": "OUT_OF_SCHEMA"})
    recorder.record({"tool": "ask_database", "statut": "refused", "code": "FORBIDDEN"})
    assert len(_lignes(journal)) == 3
    alertes = _lignes(alerte)
    assert len(alertes) == 1
    assert alertes[0]["code"] == "FORBIDDEN"


def test_les_dossiers_sont_crees_au_besoin(tmp_path):
    recorder = JsonlTraceRecorder(tmp_path / "a" / "audit.jsonl", tmp_path / "b" / "alerte.jsonl")
    recorder.record({"tool": "ask_database", "statut": "refused", "code": "FORBIDDEN"})
    assert (tmp_path / "a" / "audit.jsonl").exists()
    assert (tmp_path / "b" / "alerte.jsonl").exists()


def test_ecriture_append_jamais_ecrasement(tmp_path):
    journal, alerte = tmp_path / "audit.jsonl", tmp_path / "alerte.jsonl"
    JsonlTraceRecorder(journal, alerte).record({"statut": "ok", "code": None})
    JsonlTraceRecorder(journal, alerte).record({"statut": "ok", "code": None})
    assert len(_lignes(journal)) == 2  # une trace est immuable, on n'écrase jamais


def test_accents_lisibles_dans_le_journal(tmp_path):
    journal, alerte = tmp_path / "audit.jsonl", tmp_path / "alerte.jsonl"
    JsonlTraceRecorder(journal, alerte).record({"motif": "pertinence insuffisante", "code": None})
    assert "pertinence insuffisante" in journal.read_text("utf-8")


def test_null_recorder_n_ecrit_rien_et_ne_plante_pas():
    NullTraceRecorder().record({"statut": "ok", "code": "FORBIDDEN"})
