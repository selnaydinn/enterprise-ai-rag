import contextlib
import importlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from langchain_core.documents import Document

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_build_db():
    spec = importlib.util.spec_from_file_location("build_db", PROJECT_ROOT / "scripts" / "build_db.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def chunk(name, i, page=1):
    return Document(page_content=f"{name} metin {i}", metadata={"source_file": name, "page_number": page, "pdf_hash": f"h_{name}"})


def prepared(name, n=2):
    return {"pdf_name": name, "pdf_hash": f"h_{name}", "total_pages": 1,
            "chunks": [chunk(name, i) for i in range(n)], "ids": [f"{name}_{i}" for i in range(n)]}


class TempDirTest(unittest.TestCase):

    def setUp(self):
        self.build = load_build_db()
        self.ingestion = importlib.import_module("services.ingestion")
        self.root = Path(tempfile.mkdtemp(prefix="build_db_test_"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.pdf_dir = self.root / "pdfs"
        self.pdf_dir.mkdir()

    def write_pdf(self, name, content):
        (self.pdf_dir / name).write_bytes(content)


class PreflightTests(TempDirTest):

    def preflight(self, target=None):
        target = target or self.root / "new_db"
        return self.build.preflight(target, target.with_name(target.name + ".partial"), self.root / "m.json",
                                    self.pdf_dir, self.ingestion)

    def test_returns_sorted_pdfs_including_uppercase_extension(self):
        self.write_pdf("b.pdf", b"%PDF-b")
        self.write_pdf("A.PDF", b"%PDF-a")
        (self.pdf_dir / "notlar.txt").write_text("pdf değil")
        self.assertEqual([p.name for p in self.preflight()], ["A.PDF", "b.pdf"])

    #Hiçbir şeyin üzerine yazılmaz: hedef, yarım kalmış kurulum veya manifest varsa durur
    def test_existing_target_partial_or_manifest_stops(self):
        self.write_pdf("a.pdf", b"%PDF-a")
        for existing in ("new_db", "new_db.partial", "m.json"):
            with self.subTest(existing=existing):
                path = self.root / existing
                path.mkdir() if existing != "m.json" else path.write_text("{}")
                with self.assertRaisesRegex(RuntimeError, "zaten var"):
                    self.preflight()
                shutil.rmtree(path, True) if path.is_dir() else path.unlink()

    #Hata mesajı boş DB seçeneğini söyler
    def test_missing_or_empty_pdf_dir_stops(self):
        with self.assertRaisesRegex(RuntimeError, "PDF yok.*--allow-empty"):
            self.preflight()
        shutil.rmtree(self.pdf_dir)
        with self.assertRaisesRegex(RuntimeError, "bulunamadı.*--allow-empty"):
            self.preflight()

    #--allow-empty: PDF klasörü boşsa veya yoksa boş liste (uygulama klasörü açılışta oluşturuyor)
    def test_allow_empty_accepts_missing_or_empty_pdf_dir(self):
        target = self.root / "new_db"
        args = (target, target.with_name("new_db.partial"), self.root / "m.json", self.pdf_dir, self.ingestion)
        self.assertEqual(self.build.preflight(*args, allow_empty=True), [])
        shutil.rmtree(self.pdf_dir)
        self.assertEqual(self.build.preflight(*args, allow_empty=True), [])
        #Hedef varsa --allow-empty de durur
        target.mkdir()
        with self.assertRaisesRegex(RuntimeError, "zaten var"):
            self.build.preflight(*args, allow_empty=True)

    #Uygulama aynı içerikli ikinci PDF'i reddediyor (409); kurulumda da iki kez eklenmemeli
    def test_same_content_under_two_names_stops(self):
        self.write_pdf("a.pdf", b"%PDF-ayni")
        self.write_pdf("b.pdf", b"%PDF-ayni")
        self.write_pdf("c.pdf", b"%PDF-farkli")
        with self.assertRaisesRegex(RuntimeError, r"a\.pdf.*b\.pdf"):
            self.preflight()


class HelperTests(unittest.TestCase):

    def setUp(self):
        self.build = load_build_db()

    #Proje içindeki yol kısa, dışındaki tam yol (..\..\ ile başlayan göreli yol değil)
    def test_display_path_is_short_inside_project_and_absolute_outside(self):
        self.assertEqual(self.build.display_path(PROJECT_ROOT / "chroma_db_yeni"), "chroma_db_yeni")
        outside = Path(tempfile.gettempdir()) / "chroma_db_yeni"
        self.assertEqual(self.build.display_path(outside), str(outside.resolve()))
        self.assertNotIn("..", self.build.display_path(outside))

    def test_chunk_stats_for_empty_db(self):
        self.assertEqual(self.build.chunk_stats([], []), {"count": 0, "by_file": {}, "characters": None,
                                                           "tokens": {"median": None, "max": None, "limit": 512, "over_limit": 0}})

    def test_chunk_stats_counts_chunks_over_token_limit(self):
        stats = self.build.chunk_stats([prepared("a.pdf", 2), prepared("b.pdf", 1)], [100, 600, 513])
        self.assertEqual((stats["count"], stats["by_file"]), (3, {"a.pdf": 2, "b.pdf": 1}))
        self.assertEqual(stats["tokens"], {"median": 513, "max": 600, "limit": 512, "over_limit": 2})

    def test_token_lengths_include_special_tokens(self):
        calls = []

        def tokenizer(texts, add_special_tokens):
            calls.append(add_special_tokens)
            return {"input_ids": [[0] * len(t) for t in texts]}

        self.assertEqual(self.build.token_lengths(tokenizer, ["ab", "abcd"]), [2, 4])
        self.assertEqual(calls, [True])

    #Offline'da model yoksa ne yapılacağını söyleyen hata; indirme açıkken kütüphanenin kendi hatası
    def test_load_model_error_points_to_allow_download(self):
        def missing():
            raise OSError("not found in cache")
        with self.assertRaisesRegex(RuntimeError, "--allow-download"):
            self.build.load_model("e5", missing, allow_download=False)
        with self.assertRaises(OSError):
            self.build.load_model("e5", missing, allow_download=True)

    def test_environment_offline_by_default_and_online_with_allow_download(self):
        saved = dict(os.environ)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(saved)))
        os.environ["HF_HUB_OFFLINE"] = "1"
        self.build.configure_environment(allow_download=True)
        self.assertEqual((os.environ["HF_HUB_OFFLINE"], os.environ["TRANSFORMERS_OFFLINE"]), ("0", "0"))
        self.build.configure_environment(allow_download=False)
        self.assertEqual((os.environ["HF_HUB_OFFLINE"], os.environ["TRANSFORMERS_OFFLINE"]), ("1", "1"))


#Yeni DB'nin kopyası sahte bir chromadb istemcisiyle okunur; gerçek Chroma yok
class VerifyTests(TempDirTest):

    def setUp(self):
        super().setUp()
        self.partial = self.root / "new_db.partial"
        self.partial.mkdir()
        (self.partial / "chroma.sqlite3").write_bytes(b"x")
        self.prepared = [prepared("a.pdf", 2), prepared("b.pdf", 1)]
        self.embeddings = SimpleNamespace(embed_documents=lambda texts: [[1.0, 0.0] for _ in texts],
                                          embed_query=lambda text: [1.0, 0.0])
        patch = mock.patch.object(self.build, "release_chroma")
        patch.start()
        self.addCleanup(patch.stop)

    def stored(self):
        items = [(i, c.page_content, dict(c.metadata)) for p in self.prepared for c, i in zip(p["chunks"], p["ids"])]
        return {"ids": [i for i, _, _ in items], "documents": [d for _, d, _ in items],
                "metadatas": [m for _, _, m in items], "embeddings": [[1.0, 0.0] for _ in items]}

    def verify(self, data, space="l2"):
        self.queries = []

        def query(query_embeddings, n_results):
            self.queries.append(n_results)
            return {"ids": [data["ids"][:n_results]]}

        collection = SimpleNamespace(
            count=lambda: len(data["ids"]),
            get=lambda include: data,
            query=query,
            configuration_json={"hnsw": {"space": space}},
        )
        client = SimpleNamespace(get_collection=lambda name: collection)
        with mock.patch("chromadb.PersistentClient", return_value=client), contextlib.redirect_stdout(io.StringIO()):
            return self.build.verify(self.partial, self.prepared, self.embeddings, "langchain", "query: ")

    def test_matching_db_has_no_problems(self):
        result = self.verify(self.stored())
        self.assertEqual(result["problems"], [])
        self.assertEqual((result["count"], result["searchable"], result["consistency_min_cosine"]), (3, 3, 1.0))
        #Doğrulama kopyada yapılır; .partial yerinde kalır
        self.assertTrue(self.partial.exists())

    #Boş DB (--allow-empty): vektör karşılaştırması ve arama yapılmaz; yalnızca sayı ve mesafe ölçüsü kontrol edilir
    def test_empty_db_is_valid_without_vector_checks(self):
        self.prepared = []
        empty = {"ids": [], "documents": [], "metadatas": [], "embeddings": []}
        result = self.verify(empty)
        self.assertEqual(result["problems"], [])
        self.assertEqual((result["count"], result["searchable"], result["consistency_min_cosine"]), (0, 0, None))
        self.assertEqual(self.queries, [])
        self.assertTrue(any("l2" in p for p in self.verify(empty, space="cosine")["problems"]))
        #Beklenmeyen parça varsa boş DB geçmez
        self.assertIn("ids_match", self.verify({"ids": ["x"], "documents": ["metin"], "metadatas": [{}], "embeddings": [[1.0, 0.0]]})["problems"])

    def test_mismatches_are_reported(self):
        cases = {
            "documents_match": lambda d: d["documents"].__setitem__(0, "değişmiş metin"),
            "metadata_match": lambda d: d["metadatas"][1].__setitem__("page_number", 9),
            "ids_match": lambda d: d["ids"].__setitem__(2, "baska_id"),
        }
        for problem, change in cases.items():
            with self.subTest(problem):
                data = self.stored()
                change(data)
                self.assertIn(problem, self.verify(data)["problems"])

    def test_missing_chunks_inconsistent_vectors_and_wrong_metric_are_reported(self):
        data = self.stored()
        for key in data:
            data[key] = data[key][:2]
        self.assertTrue(any(p.startswith("count") for p in self.verify(data)["problems"]))
        data = self.stored()
        data["embeddings"] = [[0.0, 1.0] for _ in data["ids"]]
        self.assertTrue(any(p.startswith("consistency") for p in self.verify(data)["problems"]))
        self.assertTrue(any("l2" in p for p in self.verify(self.stored(), space="cosine")["problems"]))


#main() baştan sona: PDF okuma, model, Chroma ve doğrulama sahte; klasörler ve manifest gerçek (geçici klasörde)
class MainTests(TempDirTest):

    def setUp(self):
        super().setUp()
        self.write_pdf("a.pdf", b"%PDF-a")
        self.write_pdf("b.pdf", b"%PDF-b")
        self.app_db = self.root / "app_db"
        self.app_db.mkdir()
        (self.app_db / "chroma.sqlite3").write_bytes(b"uygulamanin db'si")
        self.target = self.root / "new_db"
        self.manifest = self.root / "m.json"
        saved = dict(os.environ)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(saved)))

        settings = importlib.import_module("core.settings").get_settings().model_copy(
            update={"chroma_dir": self.app_db, "upload_dir": self.pdf_dir})
        self.tokens = 10
        self.verify_result = {"problems": [], "count": 4}
        self.cross_encoder = mock.Mock()
        root = self.root

        class FakeChroma:
            def __init__(self, collection_name, persist_directory, embedding_function):
                self.dir, self.embed = Path(persist_directory), embedding_function
                self.dir.mkdir()

            def add_documents(self, documents, ids):
                self.embed.embed_documents([d.page_content for d in documents])
                with open(self.dir / "chroma.sqlite3", "a", encoding="utf-8") as f:
                    f.write("".join(ids))

        def tokenizer(texts, add_special_tokens):
            return {"input_ids": [[0] * self.tokens for _ in texts]}

        self.assertTrue(root.exists())
        patches = [
            mock.patch("core.settings.get_settings", return_value=settings),
            mock.patch.object(self.ingestion, "prepare_chunks", side_effect=lambda path, strategy=None: prepared(Path(path).name)),
            mock.patch("langchain_huggingface.HuggingFaceEmbeddings", return_value=SimpleNamespace(
                embed_documents=lambda texts: [[1.0, 0.0] for _ in texts], embed_query=lambda text: [1.0, 0.0])),
            mock.patch("transformers.AutoTokenizer", SimpleNamespace(from_pretrained=lambda name: tokenizer)),
            mock.patch("sentence_transformers.CrossEncoder", self.cross_encoder),
            mock.patch("langchain_chroma.Chroma", FakeChroma),
            mock.patch.object(self.build, "verify", side_effect=lambda *a, **k: self.verify_result),
            mock.patch.object(self.build, "release_chroma"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def run_main(self, *extra, target=None):
        argv = ["build_db.py", "--target", str(target or self.target), "--manifest", str(self.manifest), *extra]
        out = io.StringIO()
        with mock.patch.object(sys, "argv", argv), contextlib.redirect_stdout(out):
            try:
                self.build.main()
                code = 0
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue()

    def test_successful_build_moves_partial_and_writes_manifest(self):
        code, out = self.run_main()
        self.assertEqual(code, 0, out)
        self.assertTrue(self.target.exists())
        self.assertFalse(self.target.with_name("new_db.partial").exists())
        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
        self.assertEqual([p["file"] for p in manifest["pdfs"]], ["a.pdf", "b.pdf"])
        self.assertEqual(manifest["chunking"]["strategy"], "page")
        self.assertEqual((manifest["chunks"]["count"], manifest["chunks"]["tokens"]["over_limit"]), (4, 0))
        self.assertTrue(manifest["app_db_unchanged"])
        #Kurulan PDF'ler ve CHROMA_DIR ile geçiş ekrana yazılır; --allow-empty verilmediği için not yazılmaz
        self.assertIn("a.pdf", out)
        self.assertIn("CHROMA_DIR=", out)
        self.assertNotIn("dikkate alınmadı", out)
        #Varsayılan offline; reranker indirilmez
        self.assertEqual(os.environ["HF_HUB_OFFLINE"], "1")
        self.cross_encoder.assert_not_called()

    def test_failed_verification_leaves_partial_and_no_target(self):
        self.verify_result = {"problems": ["documents_match"], "count": 4}
        code, out = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("documents_match", out)
        self.assertTrue(self.target.with_name("new_db.partial").exists())
        self.assertFalse(self.target.exists())
        self.assertFalse(self.manifest.exists())

    def test_allow_download_fetches_reranker_and_goes_online(self):
        code, out = self.run_main("--allow-download")
        self.assertEqual(code, 0, out)
        self.assertEqual(os.environ["HF_HUB_OFFLINE"], "0")
        self.cross_encoder.assert_called_once()

    def test_chunks_over_token_limit_are_warned_not_fatal(self):
        self.tokens = 600
        code, out = self.run_main()
        self.assertEqual(code, 0, out)
        self.assertIn("token sınırını aşıyor", out)
        self.assertEqual(json.loads(self.manifest.read_text(encoding="utf-8"))["chunks"]["tokens"]["over_limit"], 4)

    #Uygulamanın DB'si hedef olamaz; bilinmeyen yöntem; ikisi de hiçbir şey yapmadan durur
    def test_app_db_as_target_or_unknown_strategy_stops_early(self):
        for extra, target, message in (([], self.app_db, "uygulamanın kullandığı DB"), (["--chunking", "bilinmeyen"], None, "bilinmeyen")):
            with self.subTest(message=message):
                code, _ = self.run_main(*extra, target=target)
                self.assertNotEqual(code, 0)
                self.assertIn(message, str(code))
                self.ingestion.prepare_chunks.assert_not_called()
        self.assertFalse(self.manifest.exists())
        self.assertEqual((self.app_db / "chroma.sqlite3").read_bytes(), b"uygulamanin db'si")

    #Dokümansız ilk kurulum: PDF yokken boş DB kurulur; seçenek verilmezse durur
    def test_allow_empty_builds_empty_db(self):
        for name in ("a.pdf", "b.pdf"):
            (self.pdf_dir / name).unlink()
        code, out = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("--allow-empty", out)
        self.assertFalse(self.target.exists())

        self.verify_result = {"problems": [], "count": 0}
        code, out = self.run_main("--allow-empty")
        self.assertEqual(code, 0, out)
        self.assertTrue(self.target.exists())
        self.assertIn("Boş DB kuruldu", out)
        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
        self.assertEqual((manifest["pdfs"], manifest["chunks"]["count"]), ([], 0))
        self.ingestion.prepare_chunks.assert_not_called()

    #PDF varken --allow-empty etkisiz: DB PDF'lerden kurulur ve bu ekrana yazılır
    def test_allow_empty_with_pdfs_builds_from_pdfs_and_says_so(self):
        code, out = self.run_main("--allow-empty")
        self.assertEqual(code, 0, out)
        self.assertIn("--allow-empty dikkate alınmadı", out)
        self.assertEqual(json.loads(self.manifest.read_text(encoding="utf-8"))["chunks"]["count"], 4)

    #Bir PDF okunamazsa hangisi olduğu söylenir; kuruluma geçilmez
    def test_unreadable_pdf_stops_before_building(self):
        self.ingestion.prepare_chunks.side_effect = self.ingestion.PdfUnreadableError("Dosya okunamadı")
        code, out = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("a.pdf: Dosya okunamadı", out)
        self.assertFalse(self.target.with_name("new_db.partial").exists())


if __name__ == "__main__":
    unittest.main()
