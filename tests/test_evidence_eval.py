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

ITEM = {"id": "q1", "question": "soru", "file": "a.pdf", "pages": [2]}
EVIDENCE = "kanıt cümlesi"
#Soru 0°; parçalar açıya göre sıralanır: a, b, c, d, e. Kanıt b'de (a.pdf, sayfa 2)
#Skor 1 - (2 - 2·cos)/√2: a, b, c barajın (0.72) üstünde, d ve e altında
CHUNKS = [
    ("a", 5, "a.pdf", 1, "giriş metni"),
    ("b", 15, "a.pdf", 2, "burada kanıt cümlesi geçiyor"),
    ("c", 25, "a.pdf", 3, "başka metin"),
    ("d", 60, "b.pdf", 1, "uzak metin"),
    ("e", 80, "b.pdf", 2, "en uzak metin"),
]


#eval/evaluate_evidence.py "common"ı eval/ klasöründen import ediyor ve ortam değişkenlerini (HF offline vb.)
#değiştiriyor; testi etkilemesin diye sys.path, sys.modules ve ortamı geri alıyoruz
def load_evidence_eval():
    saved_env, saved_path = dict(os.environ), list(sys.path)
    added = [name for name in ("common",) if name not in sys.modules]
    try:
        sys.path.insert(0, str(EVAL_DIR))
        spec = importlib.util.spec_from_file_location("eval_evidence", EVAL_DIR / "evaluate_evidence.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        os.environ.clear()
        os.environ.update(saved_env)
        sys.path[:] = saved_path
        for name in added:
            sys.modules.pop(name, None)


def unit(degrees):
    rad = math.radians(degrees)
    return [math.cos(rad), math.sin(rad)]


class EvaluateTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.ev = load_evidence_eval()
        cls.common = cls.ev.common

    def setUp(self):
        #Production ayarları .env'den bağımsız sabitleniyor
        for name, value in {"SCORE_THRESHOLD": 0.72, "PRODUCTION_K": 3, "RERANK_SCORE_THRESHOLD": -2.0}.items():
            patch = mock.patch.object(self.common, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        data = {
            "ids": [c[0] for c in CHUNKS],
            "embeddings": np.array([unit(c[1]) for c in CHUNKS], dtype=np.float32),
            "documents": [c[4] for c in CHUNKS],
            "metadatas": [{"source_file": c[2], "page_number": c[3]} for c in CHUNKS],
        }
        get_db = SimpleNamespace(_collection=SimpleNamespace(get=lambda include: data))
        self.collection = self.common.load_vectors(get_db, chunk_count=len(CHUNKS))
        self.embeddings = SimpleNamespace(embed_query=lambda text: unit(0))
        self.exact = {r["id"]: r for r in self.common.exact_ranking(self.collection, unit(0))}

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

    def evaluate(self, hnsw_ids, reranker=None, candidate_k=None, k=3, recall_depth=None):
        db, calls = self.hnsw_db(hnsw_ids)
        result = self.ev.evaluate(db, self.collection, self.embeddings, ITEM, EVIDENCE, k, reranker, candidate_k, recall_depth)
        return result, calls

    #HNSW dışındaki alanlar: kalite metrikleri tam sıralamadan gelmeli (rerank_seconds süre ölçümü, karşılaştırılmaz)
    def quality(self, result):
        return {key: value for key, value in result.items()
                if not key.startswith("hnsw") and key not in ("evidence_reaches_llm_hnsw", "max_distance_difference", "rerank_seconds")}

    def test_hnsw_matching_exact_ranking(self):
        result, calls = self.evaluate(list("abcde"))
        #Production derinliği (reranker yok): retrieval_k
        self.assertEqual(calls[0]["n_results"], 3)
        self.assertEqual(result["hnsw_recall"], {"k": 3, "found": 3, "recall": 1.0, "missed": []})
        self.assertFalse(result["hnsw_missed_evidence"])
        self.assertEqual((result["evidence_first_rank"], result["evidence_first_chunk"]), (2, "b"))
        self.assertTrue(result["evidence_hit_at_3"])
        self.assertTrue(result["evidence_reaches_llm_in_production"])
        self.assertTrue(result["evidence_reaches_llm_hnsw"])
        self.assertEqual(result["chunks_sent_to_llm"], 3)
        self.assertLess(result["max_distance_difference"], 1e-6)
        self.assertEqual([x["id"] for x in result["retrieved"]], ["a", "b", "c"])

    #HNSW kanıtı kaçırırsa kalite metrikleri değişmez; kaçırma yalnızca HNSW alanlarında görünür
    def test_hnsw_missing_evidence_does_not_change_quality_metrics(self):
        reference, _ = self.evaluate(list("abcde"))
        result, _ = self.evaluate(list("acde"))
        self.assertEqual(self.quality(result), self.quality(reference))
        self.assertEqual(result["hnsw_recall"]["missed"], [{"id": "b", "exact_rank": 2, "score": round(self.exact["b"]["score"], 4)}])
        #Derinlik reranker yokken 3, varken 4: bir parça kaçırıldı
        depth = result["hnsw_recall"]["k"]
        self.assertAlmostEqual(result["hnsw_recall"]["recall"], (depth - 1) / depth)
        self.assertTrue(result["hnsw_missed_evidence"])
        self.assertTrue(result["evidence_reaches_llm_in_production"])
        self.assertFalse(result["evidence_reaches_llm_hnsw"])

    def test_missing_non_evidence_chunk_is_not_missed_evidence(self):
        result, _ = self.evaluate(list("abde"))
        self.assertEqual([m["id"] for m in result["hnsw_recall"]["missed"]], ["c"])
        self.assertFalse(result["hnsw_missed_evidence"])
        self.assertTrue(result["evidence_reaches_llm_hnsw"])

    def test_distance_mismatch_stops_the_evaluation(self):
        db, _ = self.hnsw_db(list("abcde"), shift=1e-3)
        with self.assertRaisesRegex(RuntimeError, "Distance mismatch"):
            self.ev.evaluate(db, self.collection, self.embeddings, ITEM, EVIDENCE, 3)

    #Production derinliğinden (3) daha derin recall: 4. sıradaki kaçırma görünür; Gemini hesabı yine ilk 3 adayla
    def test_deeper_recall_depth_shows_later_misses(self):
        result, calls = self.evaluate(list("abce"), recall_depth=5)
        self.assertEqual(calls[0]["n_results"], 5)
        self.assertEqual(result["hnsw_recall"]["k"], 5)
        self.assertEqual([(m["id"], m["exact_rank"]) for m in result["hnsw_recall"]["missed"]], [("d", 4)])
        self.assertTrue(result["evidence_reaches_llm_hnsw"])
        #Varsayılan derinlikte (production: 3) aynı HNSW sonucu kaçırma göstermez
        self.assertEqual(self.ev.evaluate(self.hnsw_db(list("abce"))[0], self.collection, self.embeddings, ITEM, EVIDENCE, 3)
                         ["hnsw_recall"]["recall"], 1.0)


class RerankedEvaluateTests(EvaluateTests):

    def setUp(self):
        super().setUp()
        #Reranker skorları metne göre; c en yükseği alır, sonra b, sonra a
        scores = {"giriş metni": 1.0, "burada kanıt cümlesi geçiyor": 3.0, "başka metin": 5.0,
                  "uzak metin": 0.5, "en uzak metin": 0.2}
        self.predict_calls = []

        def predict(pairs):
            self.predict_calls.append([text for _, text in pairs])
            return [scores[text] for _, text in pairs]

        self.reranker = SimpleNamespace(predict=predict)

    def evaluate(self, hnsw_ids, reranker=None, candidate_k=None, k=3, recall_depth=None):
        return super().evaluate(hnsw_ids, self.reranker, 4, k, recall_depth)

    #Reranker varken production derinliği candidate_k (4); 5. sıradaki aday Gemini hesabına girmez, skorlanmaz
    def test_deeper_recall_depth_shows_later_misses(self):
        result, calls = self.evaluate(list("abcd"), recall_depth=5)
        self.assertEqual(calls[0]["n_results"], 5)
        self.assertEqual([(m["id"], m["exact_rank"]) for m in result["hnsw_recall"]["missed"]], [("e", 5)])
        self.assertEqual(len(self.predict_calls), 1)
        self.assertTrue(result["evidence_reaches_llm_hnsw"])

    def test_hnsw_matching_exact_ranking(self):
        result, calls = self.evaluate(list("abcde"))
        #Production derinliği (reranker var): candidate_k
        self.assertEqual(calls[0]["n_results"], 4)
        self.assertEqual(result["hnsw_recall"]["recall"], 1.0)
        #Reranker sırası: c, b, a, d; kanıt 2. sırada, vektör sırası 2
        self.assertEqual([x["id"] for x in result["retrieved"]], ["c", "b", "a"])
        self.assertEqual((result["evidence_first_rank"], result["evidence_vector_rank"]), (2, 2))
        self.assertTrue(result["evidence_reaches_llm_in_production"])
        self.assertTrue(result["evidence_reaches_llm_hnsw"])
        #HNSW adayları tam sıralamadakilerle aynı: reranker ikinci kez çağrılmaz
        self.assertEqual(len(self.predict_calls), 1)

    #HNSW'ye özgü aday (e) yalnızca bir kez daha skorlanır; diğer adayların skoru yeniden kullanılır
    def test_only_new_hnsw_candidates_are_rescored(self):
        result, _ = self.evaluate(list("acde"))
        self.assertEqual(self.predict_calls[1:], [["en uzak metin"]])
        self.assertTrue(result["hnsw_missed_evidence"])
        self.assertFalse(result["evidence_reaches_llm_hnsw"])

    def test_missing_non_evidence_chunk_is_not_missed_evidence(self):
        result, _ = self.evaluate(list("abde"))
        self.assertEqual([m["id"] for m in result["hnsw_recall"]["missed"]], ["c"])
        self.assertFalse(result["hnsw_missed_evidence"])
        self.assertTrue(result["evidence_reaches_llm_hnsw"])


class ProductionLlmChunksTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.ev = load_evidence_eval()

    def setUp(self):
        for name, value in {"SCORE_THRESHOLD": 0.72, "PRODUCTION_K": 2, "RERANK_SCORE_THRESHOLD": -2.0}.items():
            patch = mock.patch.object(self.ev.common, name, value)
            patch.start()
            self.addCleanup(patch.stop)

    def chunks(self, *values):
        return [{"id": cid, "score": score, "rerank_score": rerank} for cid, score, rerank in values]

    def ids(self, chunks, reranked):
        return [x["id"] for x in self.ev.production_llm_chunks(chunks, reranked)]

    #Reranker yokken ilk retrieval_k aday, sonra e5 barajı (sonraki adaylar barajı geçse de alınmaz)
    def test_without_reranker_first_k_then_threshold(self):
        chunks = self.chunks(("a", 0.80, 0), ("b", 0.70, 0), ("c", 0.90, 0))
        self.assertEqual(self.ids(chunks, reranked=False), ["a"])

    #Reranker varken: e5 barajı → reranker sırası → ilk retrieval_k; eşit skorda vektör sırası
    def test_with_reranker_threshold_then_order_then_k(self):
        chunks = self.chunks(("a", 0.80, 1.0), ("b", 0.70, 9.0), ("c", 0.75, 4.0), ("d", 0.73, 4.0))
        self.assertEqual(self.ids(chunks, reranked=True), ["c", "d"])

    #Soru düzeyinde reranker barajı: barajı geçen parçaların en iyi reranker skoru düşükse hiçbir parça gitmez
    def test_rerank_gate_uses_best_passing_score(self):
        self.assertEqual(self.ids(self.chunks(("a", 0.80, -3.0), ("b", 0.70, 5.0)), reranked=True), [])
        self.assertEqual(self.ids(self.chunks(("a", 0.80, -2.0)), reranked=True), ["a"])
        self.assertEqual(self.ids(self.chunks(("a", 0.70, 5.0)), reranked=True), [])


if __name__ == "__main__":
    unittest.main()
