import importlib
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from tests.test_upload import PDF, import_main


class DeleteEndpointTests(unittest.TestCase):

    #Gerçek uploaded_pdfs/ yerine geçici klasör; silme ayarı testlerde açık, DB silme fonksiyonu sahte
    def setUp(self):
        self.main = import_main()
        self.client = TestClient(self.main.app)
        self.upload_dir = Path(tempfile.mkdtemp(prefix="delete_test_"))
        self.addCleanup(shutil.rmtree, self.upload_dir, True)
        patches = [
            mock.patch.object(self.main, "UPLOAD_DIR", str(self.upload_dir)),
            mock.patch.object(self.main.settings, "allow_delete", True),
            mock.patch.object(self.main, "delete_document_chunks", return_value=3),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.delete_chunks = self.main.delete_document_chunks

    def delete(self, name):
        return self.client.delete(f"/documents/{name}")

    def write(self, name):
        (self.upload_dir / name).write_bytes(PDF)

    def test_deletes_chunks_and_file(self):
        self.write("rapor.pdf")
        response = self.delete("rapor.pdf")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"name": "rapor.pdf", "deleted_chunks": 3})
        self.assertFalse((self.upload_dir / "rapor.pdf").exists())
        self.delete_chunks.assert_called_once_with("rapor.pdf")

    def test_disabled_returns_403_and_changes_nothing(self):
        self.write("rapor.pdf")
        with mock.patch.object(self.main.settings, "allow_delete", False):
            response = self.delete("rapor.pdf")
        self.assertEqual(response.status_code, 403)
        self.assertTrue((self.upload_dir / "rapor.pdf").exists())
        self.delete_chunks.assert_not_called()

    def test_unknown_document_returns_404(self):
        self.delete_chunks.return_value = 0
        self.assertEqual(self.delete("yok.pdf").status_code, 404)

    def test_chunks_without_file_are_still_deleted(self):
        #Ör. dosyası elle silinmiş ama chunk'ları DB'de kalmış doküman
        response = self.delete("rapor.pdf")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["deleted_chunks"], 3)

    def test_file_without_chunks_is_still_deleted(self):
        self.delete_chunks.return_value = 0
        self.write("rapor.pdf")
        self.assertEqual(self.delete("rapor.pdf").status_code, 200)
        self.assertFalse((self.upload_dir / "rapor.pdf").exists())

    def test_invalid_names_are_rejected(self):
        #%5C = "\": "..\x.pdf" temizlenince "x.pdf" olur; istenen adla aynı olmadığı için reddediliyor
        for name in ["..%5Cx.pdf", "a.txt", ".gizli.pdf", "Rapor.PDF", "a%3Cb%3E.pdf"]:
            with self.subTest(name=name):
                self.assertEqual(self.delete(name).status_code, 400)
        self.delete_chunks.assert_not_called()

    def test_db_error_keeps_file_and_hides_details(self):
        self.write("rapor.pdf")
        self.delete_chunks.side_effect = RuntimeError("C:\\gizli\\yol chroma hatası")
        with self.assertLogs("EnterpriseAI", level="ERROR"):
            response = self.delete("rapor.pdf")
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["detail"], "Doküman silinirken beklenmeyen bir hata oluştu.")
        #DB silinemediyse dosya da silinmiyor
        self.assertTrue((self.upload_dir / "rapor.pdf").exists())

    def test_delete_runs_inside_upload_lock(self):
        lock = mock.MagicMock()
        lock.__aenter__ = mock.AsyncMock()
        lock.__aexit__ = mock.AsyncMock(return_value=False)

        def delete_chunks(name):
            #DB silme kilit alınmışken ve henüz bırakılmamışken çalışmalı
            self.assertTrue(lock.__aenter__.called)
            self.assertFalse(lock.__aexit__.called)
            return 3

        self.delete_chunks.side_effect = delete_chunks
        with mock.patch.object(self.main, "UPLOAD_LOCK", lock):
            self.assertEqual(self.delete("rapor.pdf").status_code, 200)
        self.assertTrue(lock.__aexit__.called)

    def test_features_reflect_setting(self):
        self.assertEqual(self.client.get("/features/").json(), {"allow_delete": True})
        with mock.patch.object(self.main.settings, "allow_delete", False):
            self.assertEqual(self.client.get("/features/").json(), {"allow_delete": False})

    def test_cors_allows_delete(self):
        origin = self.main.CORS_ORIGINS[0]
        response = self.client.options("/documents/rapor.pdf", headers={
            "Origin": origin,
            "Access-Control-Request-Method": "DELETE",
        })
        self.assertEqual(response.headers.get("access-control-allow-origin"), origin)


class DeleteDocumentChunksTests(unittest.TestCase):

    def setUp(self):
        self.ingestion = importlib.import_module("services.ingestion")

    def test_deletes_all_chunks_of_the_document(self):
        db = mock.Mock()
        db.get.return_value = {"ids": ["c1", "c2"]}
        with mock.patch.object(self.ingestion, "get_db", return_value=db):
            self.assertEqual(self.ingestion.delete_document_chunks("rapor.pdf"), 2)
        self.assertEqual(db.get.call_args.kwargs["where"], {"source_file": "rapor.pdf"})
        db.delete.assert_called_once_with(ids=["c1", "c2"])

    def test_nothing_to_delete(self):
        db = mock.Mock()
        db.get.return_value = {"ids": []}
        with mock.patch.object(self.ingestion, "get_db", return_value=db):
            self.assertEqual(self.ingestion.delete_document_chunks("yok.pdf"), 0)
        db.delete.assert_not_called()


if __name__ == "__main__":
    unittest.main()
