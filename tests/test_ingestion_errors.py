import importlib
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pypdf import PdfWriter
from pypdf.errors import EmptyFileError, FileNotDecryptedError, PdfReadError

READ_ERROR = "Dosya okunamadı; bozuk veya şifreli olabilir."
NO_TEXT_ERROR = "PDF'te okunabilir metin bulunamadı (taranmış olabilir)."


#Model, DB ve LLM import anında yüklenmiyor (core/config.py); services.ingestion doğrudan import edilebiliyor
def import_ingestion():
    return importlib.import_module("services.ingestion")


#pypdf hataları PdfUnreadableError'a çevriliyor; DB'ye hiçbir şey yazılmıyor
#Loader sahte: gerçek bozuk dosyada pypdf'in hangi hatayı vereceği sürüme göre değişebiliyor
class LoaderErrorTests(unittest.TestCase):

    def setUp(self):
        self.ingestion = import_ingestion()
        self.db = mock.Mock()
        patches = [
            mock.patch.object(self.ingestion, "get_db", return_value=self.db),
            mock.patch.object(self.ingestion, "PyPDFLoader"),
            mock.patch.object(self.ingestion, "hash_pdf", return_value="deadbeef"),
            mock.patch.object(self.ingestion.os.path, "exists", return_value=True),
        ]
        self.loader = patches[1].start()
        for p in [patches[0], *patches[2:]]:
            p.start()
        for p in patches:
            self.addCleanup(p.stop)

    def test_pypdf_errors_become_unreadable(self):
        for error in [PdfReadError("EOF marker not found"), FileNotDecryptedError("şifreli"), EmptyFileError("boş")]:
            with self.subTest(error=type(error).__name__):
                self.loader.return_value.load.side_effect = error
                with self.assertLogs("EnterpriseAI", level="WARNING") as logs, \
                     self.assertRaises(self.ingestion.PdfUnreadableError) as raised:
                    self.ingestion.process_pdf("rapor.pdf")
                self.assertEqual(str(raised.exception), READ_ERROR)
                #Asıl pypdf hatası zincirde duruyor; log'a yalnızca türü yazılıyor
                self.assertIs(raised.exception.__cause__, error)
                self.assertIn(type(error).__name__, "\n".join(logs.output))
                self.assertNotIn(str(error), "\n".join(logs.output))
        self.db.add_documents.assert_not_called()

    #pypdf dışındaki hatalar (örn. disk) kullanıcı kaynaklı sayılmıyor; main.py'de genel 500'e düşüyor
    def test_other_errors_are_not_converted(self):
        self.loader.return_value.load.side_effect = OSError("disk hatası")
        with self.assertLogs("EnterpriseAI", level="INFO"), self.assertRaises(OSError):
            self.ingestion.process_pdf("rapor.pdf")
        self.db.add_documents.assert_not_called()


#Gerçek PyPDFLoader ile bellekte üretilmiş PDF'ler; DB sahte
class RealPdfTests(unittest.TestCase):

    def setUp(self):
        self.ingestion = import_ingestion()
        self.db = mock.Mock()
        patch = mock.patch.object(self.ingestion, "get_db", return_value=self.db)
        patch.start()
        self.addCleanup(patch.stop)
        self.tmp = Path(tempfile.mkdtemp(prefix="ingestion_test_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def write_pdf(self, name, password=None):
        writer = PdfWriter()
        writer.add_blank_page(width=595, height=842)
        if password:
            writer.encrypt(user_password=password, algorithm="AES-256")
        path = self.tmp / name
        with open(path, "wb") as f:
            writer.write(f)
        return str(path)

    def assert_unreadable(self, path, message):
        with self.assertLogs("EnterpriseAI", level="WARNING"), \
             self.assertRaises(self.ingestion.PdfUnreadableError) as raised:
            self.ingestion.process_pdf(path)
        self.assertEqual(str(raised.exception), message)
        self.db.add_documents.assert_not_called()

    #Taranmış PDF gibi: sayfa var, metin yok; 0 parçayla "başarılı" dönmüyor
    def test_pdf_without_text_is_rejected(self):
        self.assert_unreadable(self.write_pdf("bos.pdf"), NO_TEXT_ERROR)

    #Parolasız açılamayan PDF
    def test_encrypted_pdf_is_rejected(self):
        self.assert_unreadable(self.write_pdf("sifreli.pdf", password="gizli"), READ_ERROR)


if __name__ == "__main__":
    unittest.main()
