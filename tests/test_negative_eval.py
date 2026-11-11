import importlib.util
import math
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EVAL_DIR = PROJECT_ROOT / "eval"


#eval/evaluate_negative.py "common" ve "evaluate_evidence"i eval/ klasöründen import ediyor ve ortam değişkenlerini
#(HF offline vb.) değiştiriyor; testi etkilemesin diye sys.path, sys.modules ve ortamı geri alıyoruz
def load_negative_eval():
    saved_env, saved_path = dict(os.environ), list(sys.path)
    added = [name for name in ("common", "evaluate_evidence") if name not in sys.modules]
    try:
        sys.path.insert(0, str(EVAL_DIR))
        spec = importlib.util.spec_from_file_location("eval_negative", EVAL_DIR / "evaluate_negative.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        os.environ.clear()
        os.environ.update(saved_env)
        sys.path[:] = saved_path
        for name in added:
            sys.modules.pop(name, None)


def row(group, top_e5, top_rerank, evidence_e5=None, evidence_rerank=None):
    return {"group": group, "top_e5": top_e5, "top_rerank": top_rerank,
            "evidence_e5": evidence_e5, "evidence_rerank": evidence_rerank}


#Eşik taraması saf bir fonksiyon; gerçek model ve DB kullanılmaz
class SweepTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.module = load_negative_eval()

    def setUp(self):
        self.rows = [
            row("golden", 0.85, 6.0, evidence_e5=0.84, evidence_rerank=6.0),
            #Kanıt adaylar arasında değil: her eşikte "kanıt eşik altı" sayılır
            row("golden", 0.80, 2.0),
            #Soru geçse de kanıt parçası eşiğin altında kalabilir
            row("golden", 0.82, 4.0, evidence_e5=0.75, evidence_rerank=-1.0),
            row("near_domain", 0.81, 1.0),
            row("near_domain", 0.78, -3.0),
            row("out_of_domain", 0.74, -6.0),
        ]

    def by_threshold(self, signal, thresholds):
        return {r["threshold"]: r for r in self.module.sweep(self.rows, signal, thresholds)}

    def test_threshold_is_inclusive_and_counts_each_group(self):
        t = self.by_threshold("e5", [0.80])[0.80]
        self.assertEqual(t["near_domain_accepted"], 1)
        self.assertEqual(t["out_of_domain_accepted"], 0)
        self.assertEqual(t["golden_rejected"], 0)
        #Kanıtı 0.75 olan ve kanıtı adaylarda olmayan soru
        self.assertEqual(t["golden_evidence_below"], 2)

    def test_higher_threshold_rejects_more(self):
        t = self.by_threshold("rerank", [-10.0, 3.0, 10.0])
        self.assertEqual((t[-10.0]["near_domain_accepted"], t[-10.0]["out_of_domain_accepted"], t[-10.0]["golden_rejected"]), (2, 1, 0))
        self.assertEqual((t[3.0]["near_domain_accepted"], t[3.0]["golden_rejected"], t[3.0]["golden_evidence_below"]), (0, 1, 2))
        self.assertEqual((t[10.0]["near_domain_accepted"], t[10.0]["golden_rejected"], t[10.0]["golden_evidence_below"]), (0, 3, 3))

    def test_negative_set_loader_rejects_unknown_kind(self):
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "neg.jsonl"
            path.write_text(json.dumps({"id": "n01", "kind": "other", "question": "?"}) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unknown kind"):
                self.module.load_negative(path)

    def test_real_negative_set_loads(self):
        items = self.module.load_negative(EVAL_DIR / "negative_set.jsonl")
        self.assertEqual(len(items), 55)


def unit(degrees):
    rad = math.radians(degrees)
    return [math.cos(rad), math.sin(rad)]


#Soru 0°; parçalar açıya göre sıralanır: a, b, c, d, e. Skor 1 - (2 - 2·cos)/√2: yalnızca a e5 barajının (0.72) üstünde
#Golden soruda kanıt a'da (a.pdf, sayfa 1)
CHUNKS = [
    ("a", 5, "a.pdf", 1, "kanıt cümlesi burada"),
    ("b", 40, "a.pdf", 2, "ikinci metin"),
    ("c", 50, "a.pdf", 3, "üçüncü metin"),
    ("d", 70, "b.pdf", 1, "dördüncü metin"),
    ("e", 85, "b.pdf", 2, "beşinci metin"),
]
RERANK = {"kanıt cümlesi burada": 1.0, "ikinci metin": 0.5, "üçüncü metin": -1.0, "dördüncü metin": -3.0, "beşinci metin": -4.0}
NEGATIVE = {"id": "n01", "kind": "near_domain", "question": "soru"}
GOLDEN = {"id": "q01", "question": "soru", "file": "a.pdf", "pages": [1]}
EVIDENCE = "kanıt cümlesi"


#Adaylar tam sıralamadan, HNSW ayrıca; gerçek Chroma, model ve reranker yok
class MeasureTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.module = load_negative_eval()
        cls.common = cls.module.common

    def setUp(self):
        #Production ayarları .env'den bağımsız sabitleniyor
        for name, value in {"SCORE_THRESHOLD": 0.72, "PRODUCTION_K": 3, "RERANK_SCORE_THRESHOLD": -2.0,
                            "RERANKER_ENABLED": True}.items():
            patch = mock.patch.object(self.common, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        data = {
            "ids": [c[0] for c in CHUNKS],
            "embeddings": np.array([unit(c[1]) for c in CHUNKS], dtype=np.float32),
            "documents": [c[4] for c in CHUNKS],
            "metadatas": [{"source_file": c[2], "page_number": c[3]} for c in CHUNKS],
        }
        self.collection = self.common.load_vectors(SimpleNamespace(_collection=SimpleNamespace(get=lambda include: data)), len(CHUNKS))
        self.embeddings = SimpleNamespace(embed_query=lambda text: unit(0))
        self.exact = {r["id"]: r for r in self.common.exact_ranking(self.collection, unit(0))}
        self.predict_calls = []

        def predict(pairs):
            self.predict_calls.append([text for _, text in pairs])
            return [RERANK[text] for _, text in pairs]

        self.reranker = SimpleNamespace(predict=predict)

    #HNSW'nin döndüreceği parçalar; mesafeler Chroma gibi float32 (shift ile bilerek bozulabilir)
    def hnsw_db(self, ids, shift=0.0):
        calls = []

        def query(**kwargs):
            calls.append(kwargs)
            chosen = ids[:kwargs["n_results"]]
            return {
                "ids": [chosen],
                "distances": [[float(np.float32(self.exact[i]["distance"])) + shift for i in chosen]],
                "documents": [[self.exact[i]["text"] for i in chosen]],
                "metadatas": [[self.exact[i]["metadata"] for i in chosen]],
            }

        return SimpleNamespace(_collection=SimpleNamespace(query=query)), calls

    def measure(self, hnsw_ids, item=NEGATIVE, evidence=None, recall_depth=None):
        db, calls = self.hnsw_db(hnsw_ids)
        group = "golden" if evidence is not None else item["kind"]
        result = self.module.measure(db, self.collection, self.embeddings, self.reranker, item, group, 4, evidence, recall_depth)
        return result, calls

    #Tam sıralamadan gelen alanlar (sinyaller, akış, adaylar); HNSW alanları hariç
    def signals(self, result):
        return {key: value for key, value in result.items()
                if not key.startswith("hnsw") and key not in ("sends_to_gemini_hnsw", "evidence_reaches_gemini_hnsw",
                                                               "max_distance_difference")}

    def test_hnsw_matching_exact_ranking(self):
        result, calls = self.measure(list("abcde"))
        #Production derinliği (reranker açık): candidate_k
        self.assertEqual(calls[0]["n_results"], 4)
        self.assertEqual([c["id"] for c in result["candidates"]], list("abcd"))
        self.assertEqual(result["hnsw_recall"]["recall"], 1.0)
        self.assertTrue(result["sends_to_gemini"])
        self.assertTrue(result["sends_to_gemini_hnsw"])
        self.assertFalse(result["hnsw_changes_outcome"])
        self.assertIsNone(result["hnsw_missed_evidence"])
        self.assertLess(result["max_distance_difference"], 1e-6)
        #HNSW adayları tam sıralamadakilerle aynı: reranker ikinci kez çağrılmaz
        self.assertEqual(len(self.predict_calls), 1)

    #Barajı geçen tek parça (a) HNSW'de yok: sinyaller ve akış aynı, yalnızca HNSW'nin sonucu değişir
    def test_hnsw_missing_the_only_passing_chunk_changes_only_hnsw_fields(self):
        reference, _ = self.measure(list("abcde"))
        result, _ = self.measure(list("bcde"))
        self.assertEqual(self.signals(result), self.signals(reference))
        self.assertEqual([m["id"] for m in result["hnsw_recall"]["missed"]], ["a"])
        self.assertTrue(result["sends_to_gemini"])
        self.assertFalse(result["sends_to_gemini_hnsw"])
        self.assertTrue(result["hnsw_changes_outcome"])

    #Barajı geçmeyen bir parçanın kaçırılması sonucu değiştirmez
    def test_missing_other_chunk_does_not_change_outcome(self):
        result, _ = self.measure(list("abde"))
        self.assertEqual([m["id"] for m in result["hnsw_recall"]["missed"]], ["c"])
        self.assertFalse(result["hnsw_changes_outcome"])

    def test_golden_missed_evidence(self):
        result, _ = self.measure(list("bcde"), item=GOLDEN, evidence=EVIDENCE)
        self.assertTrue(result["evidence_reaches_gemini"])
        self.assertTrue(result["hnsw_missed_evidence"])
        self.assertFalse(result["evidence_reaches_gemini_hnsw"])
        result, _ = self.measure(list("abde"), item=GOLDEN, evidence=EVIDENCE)
        self.assertFalse(result["hnsw_missed_evidence"])
        self.assertTrue(result["evidence_reaches_gemini_hnsw"])

    #HNSW'ye özgü aday (e) yalnızca bir kez daha skorlanır; diğer adayların skoru yeniden kullanılır
    def test_only_new_hnsw_candidates_are_rescored(self):
        self.measure(list("bcde"))
        self.assertEqual(self.predict_calls[1:], [["beşinci metin"]])

    #Production'da reranker kapalıysa derinlik retrieval_k ve HNSW adayları skorlanmaz
    def test_reranker_disabled_in_production(self):
        with mock.patch.object(self.common, "RERANKER_ENABLED", False):
            result, calls = self.measure(list("bcde"))
        self.assertEqual(calls[0]["n_results"], 3)
        self.assertEqual(result["hnsw_recall"]["k"], 3)
        self.assertEqual(len(self.predict_calls), 1)
        self.assertTrue(result["hnsw_changes_outcome"])

    def test_deeper_recall_depth(self):
        result, calls = self.measure(list("abcd"), recall_depth=5)
        self.assertEqual(calls[0]["n_results"], 5)
        self.assertEqual([(m["id"], m["exact_rank"]) for m in result["hnsw_recall"]["missed"]], [("e", 5)])
        self.assertFalse(result["hnsw_changes_outcome"])

    def test_distance_mismatch_stops_the_evaluation(self):
        db, _ = self.hnsw_db(list("abcde"), shift=1e-3)
        with self.assertRaisesRegex(RuntimeError, "Distance mismatch"):
            self.module.measure(db, self.collection, self.embeddings, self.reranker, NEGATIVE, "near_domain", 4)

    def test_summary_counts_hnsw_per_group(self):
        rows = [self.measure(list("abcde"))[0], self.measure(list("bcde"))[0],
                self.measure(list("bcde"), item=GOLDEN, evidence=EVIDENCE)[0]]
        hnsw = self.module.summarize(rows)["hnsw"]
        self.assertEqual(set(hnsw), {"golden", "near_domain"})
        self.assertEqual((hnsw["near_domain"]["questions_with_miss"], hnsw["near_domain"]["outcome_changed"],
                          hnsw["near_domain"]["sent_to_gemini"]), (1, 1, 1))
        self.assertEqual((hnsw["golden"]["missed_evidence"], hnsw["golden"]["evidence_reaches_gemini"]), (1, 0))
        self.assertNotIn("missed_evidence", hnsw["near_domain"])


if __name__ == "__main__":
    unittest.main()
