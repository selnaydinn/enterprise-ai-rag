import hashlib
import importlib
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

PDF = b"%PDF-1.4\n" + b"0" * 100

#Sorgu testleri gerçek usage.db'ye yazmasın; sınır testleri tests/test_usage.py'de
OPEN_USAGE = {"used": 0, "limit": 10000, "resets_at": "2026-10-24T18:00:00+03:00", "limited": False}

#Sahte process_pdf / prepare_chunks_with_metadata dönüşleri; schemas.py'deki yanıt modellerine uymalı
PROCESSED = {"filename": "rapor.pdf", "total_pages": 1, "total_chunks": 1, "message": "ok"}
ANSWER = {"status": "success", "message": "ok", "answer": "Cevap", "sources": [], "token_usage": 0}


#Model, DB ve LLM artık import anında yüklenmiyor (core/config.py); main doğrudan import edilebiliyor
#DB/LLM'e dokunan fonksiyonlar her testte sahte değerlerle değiştiriliyor
def import_main():
    return importlib.import_module("main")


class HashTests(unittest.TestCase):

    def test_hash_bytes_matches_original_format(self):
        #DB'deki pdf_hash değerleri bu formülle üretildi; değişirse duplikasyon kontrolü bozulur
        main = import_main()
        self.assertEqual(main.hash_bytes(PDF), hashlib.md5(PDF).hexdigest()[:8])

    def test_hash_pdf_equals_hash_bytes(self):
        main = import_main()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.pdf"
            path.write_bytes(PDF)
            self.assertEqual(main.hash_pdf(str(path)), main.hash_bytes(PDF))


class CleanFilenameTests(unittest.TestCase):

    def setUp(self):
        self.clean = import_main().clean_filename

    def test_directory_parts_are_removed(self):
        self.assertEqual(self.clean("../x.pdf"), "x.pdf")
        self.assertEqual(self.clean("..\\..\\x.pdf"), "x.pdf")
        self.assertEqual(self.clean("/etc/x.pdf"), "x.pdf")

    def test_extension_is_lowercased(self):
        self.assertEqual(self.clean("Rapor.PDF"), "Rapor.pdf")

    def test_turkish_and_allowed_characters_are_kept(self):
        self.assertEqual(self.clean("Öğrenci Yönetmeliği (2024)_v-1.pdf"), "Öğrenci Yönetmeliği (2024)_v-1.pdf")

    def test_unsafe_names_are_rejected(self):
        for name in ["", None, ".pdf", "..pdf", ".gizli.pdf", "a.txt", "a.pdf.exe", "a<b>.pdf", "a;b.pdf", "a\x00.pdf", "../"]:
            with self.subTest(name=name):
                self.assertIsNone(self.clean(name))


class UploadEndpointTests(unittest.TestCase):

    def setUp(self):
        self.main = import_main()
        self.client = TestClient(self.main.app)
        #Gerçek uploaded_pdfs/ yerine geçici klasör; bir üstü path traversal kontrolü için
        self.root = Path(tempfile.mkdtemp(prefix="upload_test_"))
        self.upload_dir = self.root / "uploads"
        self.upload_dir.mkdir()
        patches = [
            mock.patch.object(self.main, "UPLOAD_DIR", str(self.upload_dir)),
            mock.patch.object(self.main, "is_file_already_processed", return_value=False),
            mock.patch.object(self.main, "process_pdf", return_value=PROCESSED),
        ]
        self.in_db = patches[1].start()
        self.process = patches[2].start()
        patches[0].start()
        for p in patches:
            self.addCleanup(p.stop)
        self.addCleanup(shutil.rmtree, self.root, True)

    def upload(self, name, content=PDF):
        return self.client.post("/upload-pdf/", files={"file": (name, content, "application/pdf")})

    def files(self):
        return sorted(p.name for p in self.upload_dir.iterdir())

    def test_valid_upload_is_written_and_processed(self):
        response = self.upload("rapor.pdf")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), PROCESSED)
        self.assertEqual((self.upload_dir / "rapor.pdf").read_bytes(), PDF)
        self.process.assert_called_once_with(str(self.upload_dir / "rapor.pdf"))

    def test_path_traversal_stays_in_upload_dir(self):
        for name in ["../x.pdf", "..\\x.pdf"]:
            with self.subTest(name=name):
                response = self.upload(name)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(self.files(), ["x.pdf"])
                self.assertFalse((self.root / "x.pdf").exists())
                (self.upload_dir / "x.pdf").unlink()

    def test_uppercase_extension_is_saved_lowercase(self):
        self.assertEqual(self.upload("Rapor.PDF").status_code, 200)
        self.assertEqual(self.files(), ["Rapor.pdf"])

    def test_invalid_name_is_rejected(self):
        for name in [".pdf", "a.txt", "a<b>.pdf"]:
            with self.subTest(name=name):
                self.assertEqual(self.upload(name).status_code, 400)
        self.assertEqual(self.files(), [])
        self.process.assert_not_called()

    def test_non_pdf_content_is_rejected(self):
        response = self.upload("sahte.pdf", b"<html>pdf degil</html>")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.files(), [])
        self.process.assert_not_called()

    def test_size_limit(self):
        limit = self.main.MAX_UPLOAD_BYTES
        big = PDF + b"0" * (limit - len(PDF) + 1)
        self.assertEqual(self.upload("buyuk.pdf", big).status_code, 413)
        self.assertEqual(self.files(), [])

        exact = big[:limit]
        self.assertEqual(self.upload("tam.pdf", exact).status_code, 200)
        self.assertEqual(self.files(), ["tam.pdf"])

    def test_same_content_in_db_is_rejected_before_writing(self):
        self.in_db.return_value = True
        response = self.upload("rapor.pdf")
        self.assertEqual(response.status_code, 409)
        self.in_db.assert_called_once_with(hashlib.md5(PDF).hexdigest()[:8])
        self.assertEqual(self.files(), [])
        self.process.assert_not_called()

    def test_same_name_different_content_is_rejected_and_old_file_kept(self):
        old = b"%PDF-1.4\neski icerik"
        (self.upload_dir / "rapor.pdf").write_bytes(old)
        response = self.upload("rapor.pdf", PDF)
        self.assertEqual(response.status_code, 409)
        self.assertEqual((self.upload_dir / "rapor.pdf").read_bytes(), old)
        self.process.assert_not_called()

    def test_same_name_same_content_not_in_db_is_reprocessed(self):
        #Ör. daha önce yarıda kalmış bir yükleme: dosya diskte var ama DB'de yok
        (self.upload_dir / "rapor.pdf").write_bytes(PDF)
        self.assertEqual(self.upload("rapor.pdf").status_code, 200)
        self.process.assert_called_once()

    def test_failed_processing_removes_written_file(self):
        self.process.side_effect = RuntimeError("embedding hatası")
        response = self.upload("rapor.pdf")
        self.assertEqual(response.status_code, 500)
        self.assertEqual(self.files(), [])

    def test_failed_processing_hides_internal_error(self):
        self.process.side_effect = RuntimeError("C:\\gizli\\yol embedding hatası")
        with self.assertLogs("EnterpriseAI", level="ERROR") as logs:
            response = self.upload("rapor.pdf")
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["detail"], "Dosya işlenirken beklenmeyen bir hata oluştu.")
        #Detay kullanıcıya gitmiyor ama logda kalıyor
        self.assertIn("C:\\gizli\\yol embedding hatası", "\n".join(logs.output))


class InternalErrorTests(unittest.TestCase):

    SECRET = "C:\\gizli\\yol iç hata"

    def setUp(self):
        self.main = import_main()
        self.client = TestClient(self.main.app)

    def assert_hidden(self, response, logs, message):
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["detail"], message)
        self.assertNotIn(self.SECRET, response.text)
        self.assertIn(self.SECRET, "\n".join(logs.output))

    def test_query_error_is_hidden(self):
        with mock.patch.object(self.main, "get_status", return_value=OPEN_USAGE), \
             mock.patch.object(self.main, "prepare_chunks_with_metadata", side_effect=RuntimeError(self.SECRET)), \
             self.assertLogs("EnterpriseAI", level="ERROR") as logs:
            response = self.client.post("/query/", json={"question": "soru"})
        self.assert_hidden(response, logs, "Sorunuz yanıtlanırken beklenmeyen bir hata oluştu.")

    def test_documents_error_is_hidden(self):
        with mock.patch.object(self.main.os, "listdir", side_effect=OSError(self.SECRET)), \
             self.assertLogs("EnterpriseAI", level="ERROR") as logs:
            response = self.client.get("/documents/")
        self.assert_hidden(response, logs, "Doküman listesi alınırken bir hata oluştu.")


class QueryValidationTests(unittest.TestCase):

    def setUp(self):
        self.main = import_main()
        self.client = TestClient(self.main.app)
        patches = [
            mock.patch.object(self.main, "get_status", return_value=OPEN_USAGE),
            mock.patch.object(self.main, "record_usage"),
            mock.patch.object(self.main, "prepare_chunks_with_metadata", return_value=ANSWER),
        ]
        patches[0].start()
        patches[1].start()
        self.prepare = patches[2].start()
        for p in patches:
            self.addCleanup(p.stop)

    def ask(self, question):
        return self.client.post("/query/", json={"question": question})

    def test_question_at_limit_is_accepted(self):
        question = "a" * self.main.MAX_QUESTION_LENGTH
        self.assertEqual(self.ask(question).status_code, 200)
        self.prepare.assert_called_once_with(question)

    def test_question_over_limit_is_rejected(self):
        response = self.ask("a" * (self.main.MAX_QUESTION_LENGTH + 1))
        self.assertEqual(response.status_code, 422)
        self.prepare.assert_not_called()

    def test_empty_or_blank_question_is_rejected(self):
        for question in ["", "   ", "\n\t"]:
            with self.subTest(question=question):
                self.assertEqual(self.ask(question).status_code, 422)
        self.prepare.assert_not_called()

    def test_question_is_stripped(self):
        self.assertEqual(self.ask("  soru  ").status_code, 200)
        self.prepare.assert_called_once_with("soru")


#Yanıtlar schemas.py'deki modellere uyuyor mu
class ResponseModelTests(unittest.TestCase):

    def setUp(self):
        self.main = import_main()
        self.client = TestClient(self.main.app)
        for patch in (mock.patch.object(self.main, "get_status", return_value=OPEN_USAGE),
                      mock.patch.object(self.main, "record_usage")):
            patch.start()
            self.addCleanup(patch.stop)

    def ask(self, prepared):
        with mock.patch.object(self.main, "prepare_chunks_with_metadata", return_value=prepared):
            return self.client.post("/query/", json={"question": "soru"})

    def test_query_response_has_model_fields_only(self):
        source = {"source_name": "meb.pdf", "page": 22, "score": 0.85, "raw_text": "metin"}
        response = self.ask({**ANSWER, "sources": [source], "ic_bilgi": "gizli"})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(set(body), {"status", "message", "answer", "sources", "token_usage", "usage"})
        self.assertEqual(body["sources"], [source])
        self.assertEqual(body["usage"]["limit"], OPEN_USAGE["limit"])
        #Modelde olmayan alan yanıta sızmıyor
        self.assertNotIn("gizli", response.text)

    def test_no_result_status_is_accepted(self):
        response = self.ask({**ANSWER, "status": "no_result"})
        self.assertEqual(response.json()["status"], "no_result")

    def test_malformed_query_result_returns_generic_500(self):
        #Eski biçim ("Success", "data") artık modele uymuyor; ham hata yerine genel mesaj dönmeli
        with self.assertLogs("EnterpriseAI", level="ERROR"):
            response = self.ask({"status": "Success", "data": "Cevap", "token_usage": 0})
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["detail"], "Sorunuz yanıtlanırken beklenmeyen bir hata oluştu.")

    def test_usage_endpoint_matches_model(self):
        response = self.client.get("/usage/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.json()), {"used", "limit", "resets_at", "limited"})


class CorsTests(unittest.TestCase):

    def setUp(self):
        self.main = import_main()
        self.client = TestClient(self.main.app)

    def preflight(self, origin):
        return self.client.options("/query/", headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type",
        })

    def test_allowed_origin_gets_cors_header(self):
        #.env'de CORS_ORIGINS tanımlıysa onu kullanıyoruz; varsayılana bağımlı kalmasın
        origin = self.main.CORS_ORIGINS[0]
        response = self.preflight(origin)
        self.assertEqual(response.headers.get("access-control-allow-origin"), origin)
        self.assertNotIn("access-control-allow-credentials", response.headers)

    def test_unknown_origin_gets_no_cors_header(self):
        response = self.preflight("http://kotu-site.example")
        self.assertNotIn("access-control-allow-origin", response.headers)


class ConcurrencyTests(unittest.TestCase):

    #TestClient "with" ile açılıyor: eş istekler aynı event loop'ta çalışsın (UPLOAD_LOCK tek bir loop'a bağlanır)
    #"with" lifespan'ı da çalıştırıyor; gerçek model/Gemini yüklenmesin diye get_llm ve get_db sahte
    def setUp(self):
        self.main = import_main()
        self.upload_dir = Path(tempfile.mkdtemp(prefix="upload_test_"))
        self.addCleanup(shutil.rmtree, self.upload_dir, True)
        for patch in (mock.patch.object(self.main, "UPLOAD_DIR", str(self.upload_dir)),
                      mock.patch.object(self.main, "get_llm"),
                      mock.patch.object(self.main, "get_db")):
            patch.start()
            self.addCleanup(patch.stop)
        self.client = TestClient(self.main.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def upload(self, name):
        return self.client.post("/upload-pdf/", files={"file": (name, PDF, "application/pdf")})

    def test_upload_does_not_block_other_requests(self):
        started, release, listed = threading.Event(), threading.Event(), threading.Event()
        #Test başarısız olsa da yükleme thread'i takılı kalmasın
        self.addCleanup(release.set)

        def slow_process(path):
            started.set()
            release.wait(5)
            return PROCESSED

        def list_documents():
            if self.client.get("/documents/").status_code == 200:
                listed.set()

        with mock.patch.object(self.main, "is_file_already_processed", return_value=False), \
             mock.patch.object(self.main, "process_pdf", side_effect=slow_process):
            uploader = threading.Thread(target=self.upload, args=("rapor.pdf",))
            uploader.start()
            self.assertTrue(started.wait(5))
            #Embedding sürerken liste isteği yanıt vermeli
            threading.Thread(target=list_documents).start()
            self.assertTrue(listed.wait(2), "Upload sürerken /documents/ yanıt vermedi")
            release.set()
            uploader.join(5)

    def test_concurrent_same_pdf_is_processed_once(self):
        #DB sahte: process_pdf bitince hash "kayıtlı" sayılıyor
        processed = set()

        def slow_process(path):
            time.sleep(0.5)
            processed.add(self.main.hash_bytes(PDF))
            return PROCESSED

        codes = []
        with mock.patch.object(self.main, "is_file_already_processed", side_effect=lambda h: h in processed), \
             mock.patch.object(self.main, "process_pdf", side_effect=slow_process) as process:
            #Farklı adlar: aynı ad kontrolüne değil, DB duplikasyon kontrolüne takılsın
            threads = [threading.Thread(target=lambda n=name: codes.append(self.upload(n).status_code))
                       for name in ("a.pdf", "b.pdf")]
            for t in threads:
                t.start()
            for t in threads:
                t.join(10)
        self.assertEqual(sorted(codes), [200, 409])
        process.assert_called_once()


if __name__ == "__main__":
    unittest.main()
