import importlib.util
import math
import os
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]


#eval/common.py import edilirken ortam değişkenlerini (HF offline vb.) değiştiriyor; testi etkilemesin diye geri alıyoruz
def load_eval_module():
    saved = dict(os.environ)
    try:
        spec = importlib.util.spec_from_file_location("eval_common", PROJECT_ROOT / "eval" / "common.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        os.environ.clear()
        os.environ.update(saved)


def meta(page=1, source="a.pdf"):
    return {"source_file": source, "page_number": page}


#Chroma koleksiyonunun get() yanıtını taklit eden sahte DB; gerçek Chroma kullanılmaz
def fake_db(ids, embeddings, documents=None, metadatas=None):
    data = {
        "ids": ids,
        "embeddings": embeddings,
        "documents": documents if documents is not None else [f"metin {i}" for i in ids],
        "metadatas": metadatas if metadatas is not None else [meta() for _ in ids],
    }
    calls = []

    def get(include):
        calls.append(include)
        return data

    return SimpleNamespace(_collection=SimpleNamespace(get=get)), calls


class LoadVectorsTests(unittest.TestCase):

    def setUp(self):
        self.common = load_eval_module()

    def test_returns_all_fields_sorted_by_id(self):
        #get() sırası garanti değil; sonuç id'ye göre sıralı ve alanlar birlikte taşınıyor
        db, calls = fake_db(
            ids=["c", "a", "b"],
            embeddings=np.array([[0.0, 1.0], [1.0, 0.0], [0.6, 0.8]], dtype=np.float32),
            documents=["metin c", "metin a", "metin b"],
            metadatas=[meta(3), meta(1), meta(2)],
        )
        loaded = self.common.load_vectors(db, chunk_count=3)
        self.assertEqual(loaded.ids, ["a", "b", "c"])
        self.assertEqual(loaded.documents, ["metin a", "metin b", "metin c"])
        self.assertEqual([m["page_number"] for m in loaded.metadatas], [1, 2, 3])
        #Beklenen değer de float32: 0.6'nın float32 ve float64 karşılıkları birebir eşit değil
        np.testing.assert_array_equal(loaded.vectors, np.array([[1.0, 0.0], [0.6, 0.8], [0.0, 1.0]], dtype=np.float32))
        self.assertEqual(loaded.vectors.dtype, np.float32)
        self.assertEqual(calls, [["embeddings", "documents", "metadatas"]])

    def test_accepts_plain_lists(self):
        db, _ = fake_db(ids=["a"], embeddings=[[0.6, 0.8]])
        self.assertEqual(self.common.load_vectors(db, chunk_count=1).vectors.shape, (1, 2))

    def test_count_mismatch_is_rejected(self):
        db, _ = fake_db(ids=["a", "b"], embeddings=[[1.0, 0.0], [0.0, 1.0]])
        with self.assertRaisesRegex(RuntimeError, "returned 2 chunks, collection count is 3"):
            self.common.load_vectors(db, chunk_count=3)

    def test_missing_or_short_fields_are_rejected(self):
        cases = {
            "embeddings yok": dict(embeddings=None),
            "eksik embedding": dict(embeddings=[[1.0, 0.0]]),
            "eksik metin": dict(embeddings=[[1.0, 0.0], [0.0, 1.0]], documents=["metin a"]),
            "eksik metadata": dict(embeddings=[[1.0, 0.0], [0.0, 1.0]], metadatas=[meta()]),
        }
        for name, fields in cases.items():
            with self.subTest(name):
                db, _ = fake_db(ids=["a", "b"], **fields)
                with self.assertRaises(RuntimeError):
                    self.common.load_vectors(db, chunk_count=2)

    def test_duplicate_ids_are_rejected(self):
        db, _ = fake_db(ids=["a", "a"], embeddings=[[1.0, 0.0], [0.0, 1.0]])
        with self.assertRaisesRegex(RuntimeError, "Duplicate"):
            self.common.load_vectors(db, chunk_count=2)

    def test_bad_vectors_are_rejected(self):
        cases = {
            "farklı boyut": [[1.0, 0.0], [1.0, 0.0, 0.0]],
            "boş vektör": [[], []],
            "NaN": [[1.0, 0.0], [float("nan"), 1.0]],
            "sonsuz": [[1.0, 0.0], [float("inf"), 1.0]],
            "sıfır vektör": [[1.0, 0.0], [0.0, 0.0]],
        }
        for name, embeddings in cases.items():
            with self.subTest(name):
                db, _ = fake_db(ids=["a", "b"], embeddings=embeddings)
                with self.assertRaises(RuntimeError):
                    self.common.load_vectors(db, chunk_count=2)

    def test_chunks_without_text_or_required_metadata_are_rejected(self):
        cases = {
            "boş metin": dict(documents=["metin a", ""]),
            "metin None": dict(documents=["metin a", None]),
            "metadata None": dict(metadatas=[meta(), None]),
            "source_file yok": dict(metadatas=[meta(), {"page_number": 1}]),
            "page_number yok": dict(metadatas=[meta(), {"source_file": "a.pdf"}]),
        }
        for name, fields in cases.items():
            with self.subTest(name):
                db, _ = fake_db(ids=["a", "b"], embeddings=[[1.0, 0.0], [0.0, 1.0]], **fields)
                with self.assertRaisesRegex(RuntimeError, "Chunk b"):
                    self.common.load_vectors(db, chunk_count=2)


def unit(degrees):
    rad = math.radians(degrees)
    return [math.cos(rad), math.sin(rad)]


class ExactRankingTests(unittest.TestCase):

    def setUp(self):
        self.common = load_eval_module()

    def collection(self, ids, embeddings):
        db, _ = fake_db(ids=ids, embeddings=embeddings, metadatas=[meta(page=n) for n, _ in enumerate(ids, 1)])
        return self.common.load_vectors(db, chunk_count=len(ids))

    #Birim vektörlerde karesi alınmış öklid mesafesi 2 - 2·cos(açı); sıra açıya göre
    def test_order_distance_and_score_match_hand_calculation(self):
        collection = self.collection(["c", "a", "b"], [unit(90), unit(0), unit(30)])
        ranked = self.common.exact_ranking(collection, unit(10))
        self.assertEqual([r["id"] for r in ranked], ["a", "b", "c"])
        self.assertEqual([r["rank"] for r in ranked], [1, 2, 3])
        for r, angle in zip(ranked, (10, 20, 80)):
            expected = 2 - 2 * math.cos(math.radians(angle))
            #Vektörler float32 saklandığı için tam eşitlik değil, float32 hassasiyeti
            self.assertAlmostEqual(r["distance"], expected, places=6)
            self.assertAlmostEqual(r["score"], 1 - expected / math.sqrt(2), places=6)

    def test_text_and_metadata_follow_their_chunk(self):
        collection = self.collection(["a", "b"], [unit(90), unit(0)])
        top = self.common.exact_ranking(collection, unit(0))[0]
        self.assertEqual((top["id"], top["text"], top["metadata"]["page_number"]), ("b", "metin b", 2))

    #Production ve eski eval sonuçlarıyla aynı ölçek: Chroma'nın l2 mesafesi ve LangChain'in l2 skor fonksiyonu
    def test_matches_chroma_distance_and_langchain_score(self):
        from chromadb.utils.distance_functions import l2
        from langchain_core.vectorstores import VectorStore
        rng = np.random.default_rng(0)
        embeddings = rng.normal(size=(5, 8)).astype(np.float32)
        embeddings /= np.linalg.norm(embeddings, axis=1, keepdims=True)
        query = rng.normal(size=8)
        query /= np.linalg.norm(query)
        collection = self.collection(list("abcde"), embeddings)
        for r in self.common.exact_ranking(collection, query):
            vector = collection.vectors[collection.ids.index(r["id"])].astype(np.float64)
            self.assertAlmostEqual(r["distance"], l2(vector, query), places=9)
            self.assertAlmostEqual(r["score"], VectorStore._euclidean_relevance_score_fn(r["distance"]), places=12)

    def test_returns_whole_collection_sorted_by_distance(self):
        rng = np.random.default_rng(1)
        collection = self.collection([f"id{i:02d}" for i in range(20)], rng.normal(size=(20, 4)))
        ranked = self.common.exact_ranking(collection, rng.normal(size=4))
        self.assertEqual(len(ranked), 20)
        self.assertEqual(sorted(r["id"] for r in ranked), collection.ids)
        distances = [r["distance"] for r in ranked]
        self.assertEqual(distances, sorted(distances))

    #Aynı vektörlü parçalar her çalıştırmada id sırasıyla; get() hangi sırayla dönerse dönsün
    def test_equal_distances_keep_id_order(self):
        for ids in (["b", "a", "c"], ["c", "b", "a"], ["a", "c", "b"]):
            with self.subTest(ids=ids):
                collection = self.collection(ids, [unit(30), unit(30), unit(30)])
                self.assertEqual([r["id"] for r in self.common.exact_ranking(collection, unit(0))], ["a", "b", "c"])

    def test_bad_query_vector_is_rejected(self):
        collection = self.collection(["a"], [unit(0)])
        for name, query in {"yanlış boyut": [1.0, 0.0, 0.0], "matris": [[1.0, 0.0]], "NaN": [float("nan"), 0.0]}.items():
            with self.subTest(name):
                with self.assertRaises(RuntimeError):
                    self.common.exact_ranking(collection, query)


#Chroma koleksiyonunun query() yanıtını taklit eden sahte DB (sonuçlar tek soru için iç içe listede)
def fake_query_db(ids, distances, documents=None, metadatas=None):
    result = {
        "ids": [ids],
        "distances": [distances],
        "documents": [documents if documents is not None else [f"metin {i}" for i in ids]],
        "metadatas": [metadatas if metadatas is not None else [meta(page=n) for n, _ in enumerate(ids, 1)]],
    }
    calls = []

    def query(**kwargs):
        calls.append(kwargs)
        return result

    return SimpleNamespace(_collection=SimpleNamespace(query=query)), calls


class HnswRankingTests(unittest.TestCase):

    def setUp(self):
        self.common = load_eval_module()

    #Production'daki LangChain çağrısıyla aynı: tek soru vektörü, n_results=k, metin + metadata + mesafe
    def test_queries_chroma_with_given_vector_and_k(self):
        db, calls = fake_query_db(["a", "b"], [0.1, 0.2])
        self.common.hnsw_ranking(db, np.array([0.6, 0.8], dtype=np.float32), k=2)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["n_results"], 2)
        self.assertEqual(calls[0]["include"], ["documents", "metadatas", "distances"])
        #Vektör olduğu gibi gidiyor (embed_query'nin döndürdüğü gibi Python float listesi)
        self.assertEqual(calls[0]["query_embeddings"], [[float(np.float32(0.6)), float(np.float32(0.8))]])
        self.assertTrue(all(type(x) is float for x in calls[0]["query_embeddings"][0]))

    def test_output_has_same_fields_and_scale_as_exact_ranking(self):
        from langchain_core.vectorstores import VectorStore
        db, _ = fake_query_db(["b", "a"], [0.05, 0.3], documents=["metin b", "metin a"], metadatas=[meta(7), meta(2)])
        ranked = self.common.hnsw_ranking(db, unit(0), k=2)
        self.assertEqual([r["rank"] for r in ranked], [1, 2])
        self.assertEqual([(r["id"], r["text"], r["metadata"]["page_number"]) for r in ranked], [("b", "metin b", 7), ("a", "metin a", 2)])
        self.assertEqual([r["distance"] for r in ranked], [0.05, 0.3])
        for r in ranked:
            self.assertAlmostEqual(r["score"], VectorStore._euclidean_relevance_score_fn(r["distance"]), places=12)
        #exact_ranking ile aynı alanlar: ikisi recall ve mesafe kontrolünde yan yana kullanılacak
        collection = self.common.load_vectors(fake_db(["a"], [unit(0)])[0], chunk_count=1)
        self.assertEqual(set(ranked[0]), set(self.common.exact_ranking(collection, unit(0))[0]))

    #HNSW parça kaçırabilir veya koleksiyon k'dan küçük olabilir; eksik sonuç hata değil (recall ile ölçülür)
    def test_fewer_results_than_k_are_returned_as_is(self):
        db, _ = fake_query_db(["a"], [0.1])
        self.assertEqual([r["id"] for r in self.common.hnsw_ranking(db, unit(0), k=10)], ["a"])
        db, _ = fake_query_db([], [])
        self.assertEqual(self.common.hnsw_ranking(db, unit(0), k=3), [])

    def test_invalid_k_is_rejected_before_querying(self):
        for k in (0, -1, 2.0, True, None):
            with self.subTest(k=k):
                db, calls = fake_query_db(["a"], [0.1])
                with self.assertRaises(RuntimeError):
                    self.common.hnsw_ranking(db, unit(0), k=k)
                self.assertEqual(calls, [])

    def test_bad_query_vector_is_rejected_before_querying(self):
        for query in ([float("nan"), 0.0], [float("inf"), 0.0]):
            with self.subTest(query=query):
                db, calls = fake_query_db(["a"], [0.1])
                with self.assertRaises(RuntimeError):
                    self.common.hnsw_ranking(db, query, k=1)
                self.assertEqual(calls, [])

    def test_malformed_query_result_is_rejected(self):
        cases = {
            "farklı uzunluk": dict(ids=["a", "b"], distances=[0.1]),
            "k'dan fazla sonuç": dict(ids=["a", "b", "c"], distances=[0.1, 0.2, 0.3]),
            "tekrarlanan id": dict(ids=["a", "a"], distances=[0.1, 0.2]),
        }
        for name, fields in cases.items():
            with self.subTest(name):
                db, _ = fake_query_db(**fields)
                with self.assertRaises(RuntimeError):
                    self.common.hnsw_ranking(db, unit(0), k=2)


def ranked(*pairs):
    return [{"id": cid, "distance": d} for cid, d in pairs]


class DistanceAgreementTests(unittest.TestCase):

    def setUp(self):
        self.common = load_eval_module()

    def test_returns_largest_difference_when_within_tolerance(self):
        exact = ranked(("a", 0.10), ("b", 0.20), ("c", 0.30))
        hnsw = ranked(("b", 0.20 + 3e-7), ("a", 0.10 - 1e-6))
        self.assertAlmostEqual(self.common.check_distance_agreement(exact, hnsw), 1e-6, places=12)

    #HNSW'nin kaçırdığı parça (c) bu kontrolün konusu değil
    def test_missing_chunks_in_hnsw_are_not_an_error(self):
        exact = ranked(("a", 0.10), ("b", 0.20), ("c", 0.30))
        self.assertEqual(self.common.check_distance_agreement(exact, ranked(("a", 0.10))), 0.0)
        self.assertEqual(self.common.check_distance_agreement(exact, []), 0.0)

    def test_difference_above_tolerance_is_rejected(self):
        exact = ranked(("a", 0.10), ("b", 0.20))
        with self.assertRaisesRegex(RuntimeError, "Distance mismatch for chunk b"):
            self.common.check_distance_agreement(exact, ranked(("a", 0.10), ("b", 0.201)))

    def test_tolerance_is_inclusive_and_can_be_changed(self):
        exact = ranked(("a", 0.5))
        self.assertEqual(self.common.check_distance_agreement(exact, ranked(("a", 0.5)), tolerance=0.0), 0.0)
        with self.assertRaises(RuntimeError):
            self.common.check_distance_agreement(exact, ranked(("a", 0.5 + 1e-6)), tolerance=1e-7)

    def test_nan_distance_is_rejected(self):
        with self.assertRaises(RuntimeError):
            self.common.check_distance_agreement(ranked(("a", 0.1)), ranked(("a", float("nan"))))

    def test_unknown_chunk_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "chunk x that is not in the collection"):
            self.common.check_distance_agreement(ranked(("a", 0.1)), ranked(("x", 0.1)))

    #DB "cosine" ile kurulsaydı Chroma 1 - cos döner; birim vektörlerde l2 = 2 - 2·cos, fark tolerans dışında kalır
    def test_cosine_distances_would_be_caught(self):
        collection = self.common.load_vectors(fake_db(["a", "b"], [unit(0), unit(60)])[0], chunk_count=2)
        exact = self.common.exact_ranking(collection, unit(20))
        cosine_hnsw = [{"id": r["id"], "distance": 1 - (1 - r["distance"] / 2)} for r in exact]
        with self.assertRaises(RuntimeError):
            self.common.check_distance_agreement(exact, cosine_hnsw)

    #Gerçek akış: exact ve hnsw çıktıları birlikte, float32'ye yuvarlanmış Chroma mesafeleriyle
    def test_works_with_exact_and_hnsw_outputs(self):
        collection = self.common.load_vectors(fake_db(["a", "b", "c"], [unit(0), unit(30), unit(90)])[0], chunk_count=3)
        exact = self.common.exact_ranking(collection, unit(10))
        chroma_distances = [float(np.float32(r["distance"])) for r in exact[:2]]
        db, _ = fake_query_db([r["id"] for r in exact[:2]], chroma_distances)
        hnsw = self.common.hnsw_ranking(db, unit(10), k=2)
        self.assertLess(self.common.check_distance_agreement(exact, hnsw), 1e-6)


def ranking(ids):
    return [{"rank": n, "id": cid, "score": 1.0 - n / 100} for n, cid in enumerate(ids, 1)]


class RecallTests(unittest.TestCase):

    def setUp(self):
        self.common = load_eval_module()

    def test_full_match_is_one(self):
        result = self.common.recall_at_k(ranking("abc"), ranking("abc"), k=3)
        self.assertEqual(result, {"k": 3, "found": 3, "recall": 1.0, "missed": []})

    #Sıra farkı kaçırma sayılmaz
    def test_order_difference_does_not_lower_recall(self):
        self.assertEqual(self.common.recall_at_k(ranking("abc"), ranking("cab"), k=3)["recall"], 1.0)

    #q21 örneği: tam sırada 3. olan parça HNSW'de yok, yerine 11. parça girmiş
    def test_missed_chunk_is_reported_with_its_exact_rank(self):
        exact = ranking("abcdefghijk")
        hnsw = ranking("abdefghijk")
        result = self.common.recall_at_k(exact, hnsw, k=10)
        self.assertEqual((result["k"], result["found"]), (10, 9))
        self.assertAlmostEqual(result["recall"], 0.9)
        self.assertEqual(result["missed"], [{"id": "c", "exact_rank": 3, "score": 0.97}])

    #Derinlik önemli: 4. sıradaki kaçırma ilk 3'te görünmez, ilk 10'da görünür
    def test_depth_decides_which_misses_count(self):
        exact = ranking("abcdefghijk")
        hnsw = ranking("abcefghijkl")
        self.assertEqual(self.common.recall_at_k(exact, hnsw, k=3)["recall"], 1.0)
        self.assertEqual(self.common.recall_at_k(exact, hnsw, k=10)["missed"][0]["exact_rank"], 4)

    #HNSW'nin k'dan sonraki sonuçları sayılmaz: 11. sırada bulunan parça ilk 10 için kaçırılmış sayılır
    def test_only_first_k_of_hnsw_count(self):
        exact = ranking("abcdefghij")
        hnsw = ranking("abcdefghiXj")
        self.assertEqual([m["id"] for m in self.common.recall_at_k(exact, hnsw, k=10)["missed"]], ["j"])

    def test_short_or_empty_lists(self):
        #Koleksiyon k'dan küçükse derinlik koleksiyon boyutu
        self.assertEqual(self.common.recall_at_k(ranking("ab"), ranking("ab"), k=10)["k"], 2)
        #HNSW eksik döndüyse eksikler kaçırılmış sayılır
        result = self.common.recall_at_k(ranking("abc"), ranking("a"), k=3)
        self.assertEqual((result["found"], [m["id"] for m in result["missed"]]), (1, ["b", "c"]))
        #Boş koleksiyon: kaçırılacak parça yok
        self.assertEqual(self.common.recall_at_k([], [], k=3), {"k": 0, "found": 0, "recall": 1.0, "missed": []})

    def test_invalid_k_is_rejected(self):
        for k in (0, -1, 2.0, True, None):
            with self.subTest(k=k), self.assertRaises(RuntimeError):
                self.common.recall_at_k(ranking("a"), ranking("a"), k=k)

    #Gerçek akış: exact ve hnsw çıktıları birlikte
    def test_works_with_exact_and_hnsw_outputs(self):
        collection = self.common.load_vectors(fake_db(["a", "b", "c"], [unit(0), unit(30), unit(90)])[0], chunk_count=3)
        exact = self.common.exact_ranking(collection, unit(10))
        db, _ = fake_query_db(["a", "c"], [exact[0]["distance"], exact[2]["distance"]])
        result = self.common.recall_at_k(exact, self.common.hnsw_ranking(db, unit(10), k=2), k=2)
        self.assertEqual((result["recall"], [m["id"] for m in result["missed"]]), (0.5, ["b"]))


if __name__ == "__main__":
    unittest.main()
