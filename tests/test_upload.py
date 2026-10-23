import hashlib
import importlib
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from fastapi.testclient import TestClient

PDF = b"%PDF-1.4\n" + b"0" * 100


_main = None


#main.py import edilirken core.config modeli, DB'yi ve Gemini'yi yüklüyor; services.query de config'e bağlı
#İkisini sahte modülle değiştirip bir kez import ediyoruz; gerçek ingestion yükleniyor ama içindeki db None oluyor
#(Yalnızca bu girdileri geri alıyoruz; tüm sys.modules'ü geri almak numpy gibi modülleri bozar)
def import_main():
    global _main
    if _main is None:
        missing = object()
        names = ("core.config", "services.query", "services.ingestion", "main")
        saved = {name: sys.modules.get(name, missing) for name in names}
        sys.modules["core.config"] = SimpleNamespace(db=None, llm=None)
        sys.modules["services.query"] = SimpleNamespace(prepare_chunks_with_metadata=None)
        sys.modules.pop("services.ingestion", None)
        sys.modules.pop("main", None)
        try:
            _main = importlib.import_module("main")
        finally:
            for name, module in saved.items():
                if module is missing:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = module
    return _main


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
            mock.patch.object(self.main, "process_pdf", return_value={"status": "Success"}),
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


if __name__ == "__main__":
    unittest.main()
