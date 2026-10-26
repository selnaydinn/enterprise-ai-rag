import importlib
import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PDF_PATH = PROJECT_ROOT / "uploaded_pdfs" / "meb_yonetmelik.pdf"

#Boşluk içeren boş satır: "\n" + yalnızca boşluk/tab + "\n"
SPACED_BLANK = re.compile(r"\n[^\S\n]+\n")


#Model, DB ve LLM artık import anında yüklenmiyor (core/config.py); services.ingestion doğrudan import edilebiliyor
def import_ingestion():
    return importlib.import_module("services.ingestion")


class NormalizeBlankLinesTests(unittest.TestCase):

    def setUp(self):
        self.normalize = import_ingestion().normalize_blank_lines

    def test_spaced_blank_line_becomes_paragraph_separator(self):
        self.assertEqual(self.normalize("birinci \n \nikinci"), "birinci \n\nikinci")

    def test_tabs_and_multiple_spaces_are_also_blank(self):
        self.assertEqual(self.normalize("a\n \t  \nb"), "a\n\nb")

    def test_consecutive_spaced_blank_lines_all_become_empty(self):
        self.assertEqual(self.normalize("a\n \n  \nb"), "a\n\n\nb")

    def test_other_line_breaks_and_trailing_spaces_are_untouched(self):
        text = "MADDE 9 - (1) Bir ders \nsaati süresi 40 dakikadır. \n(2) Dersler"
        self.assertEqual(self.normalize(text), text)

    def test_existing_paragraph_separator_is_untouched(self):
        self.assertEqual(self.normalize("a\n\nb"), "a\n\nb")


class PdfNormalizationCountsTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if not PDF_PATH.is_file():
            raise unittest.SkipTest(f"{PDF_PATH} bulunamadı (uploaded_pdfs/ git'e dahil değil)")
        from langchain_community.document_loaders import PyPDFLoader
        cls.before = [p.page_content for p in PyPDFLoader(str(PDF_PATH)).load()]
        normalize = import_ingestion().normalize_blank_lines
        cls.after = [normalize(t) for t in cls.before]

    def counts(self, texts):
        joined = "\n".join(texts)
        return {"\\n\\n": joined.count("\n\n"), "\\n \\n": len(SPACED_BLANK.findall(joined))}

    def test_spaced_blank_lines_are_converted(self):
        before, after = self.counts(self.before), self.counts(self.after)
        print(f"\n  meb_yonetmelik.pdf önce: {before} | sonra: {after}")
        self.assertGreater(before["\\n \\n"], 0)
        self.assertEqual(after["\\n \\n"], 0)
        self.assertGreater(after["\\n\\n"], before["\\n\\n"])

    def test_only_whitespace_lines_change(self):
        #Satır sayısı aynı kalmalı; dolu satırlar birebir korunmalı, boşluktan ibaret satırlar boşalmalı
        for n, (before, after) in enumerate(zip(self.before, self.after), 1):
            with self.subTest(page=n):
                self.assertEqual(after.split("\n"), [line if line.strip() else "" for line in before.split("\n")])


class ProcessPdfAppliesNormalizationTests(unittest.TestCase):

    def test_splitter_receives_normalized_pages(self):
        #Sahte DB: hiçbir şey yazılmaz; gerçek PDF yerine sahte loader kullanılır
        fake_db = mock.Mock()
        fake_db.get.return_value = {"ids": []}
        ingestion = import_ingestion()
        page = SimpleNamespace(page_content="MADDE 1 - (1) Amaç \n \n(2) Kapsam", metadata={"page": 0})
        captured = {}

        def fake_split(pages):
            captured["texts"] = [p.page_content for p in pages]
            return []

        with mock.patch.object(ingestion, "get_db", return_value=fake_db), \
             mock.patch.object(ingestion, "PyPDFLoader") as loader, \
             mock.patch.object(ingestion.RecursiveCharacterTextSplitter, "split_documents", side_effect=fake_split), \
             mock.patch.object(ingestion, "hash_pdf", return_value="deadbeef"), \
             mock.patch.object(ingestion.os.path, "exists", return_value=True):
            loader.return_value.load.return_value = [page]
            #process_pdf'in INFO logları test çıktısına karışmasın diye yakalıyoruz
            with self.assertLogs("EnterpriseAI", level="INFO"):
                ingestion.process_pdf("sahte.pdf")

        self.assertEqual(captured["texts"], ["MADDE 1 - (1) Amaç \n\n(2) Kapsam"])
        fake_db.add_documents.assert_not_called()
        #Duplikasyon kontrolü main.py'de; process_pdf DB'yi sorgulamıyor
        fake_db.get.assert_not_called()

    #Hata 200 ile dönen bir sözlük değil exception; main.py bunu yakalayıp dosyayı temizliyor ve 500 veriyor
    def test_missing_file_raises(self):
        ingestion = import_ingestion()
        with mock.patch.object(ingestion, "get_db") as get_db, \
             self.assertRaisesRegex(FileNotFoundError, "olmayan_dosya.pdf"):
            ingestion.process_pdf("olmayan_dosya.pdf")
        get_db.assert_not_called()

    def test_success_result_has_upload_fields(self):
        ingestion = import_ingestion()
        page = SimpleNamespace(page_content="MADDE 1", metadata={"page": 0})
        with mock.patch.object(ingestion, "get_db"), \
             mock.patch.object(ingestion, "PyPDFLoader") as loader, \
             mock.patch.object(ingestion, "hash_pdf", return_value="deadbeef"), \
             mock.patch.object(ingestion.os.path, "exists", return_value=True), \
             self.assertLogs("EnterpriseAI", level="INFO"):
            loader.return_value.load.return_value = [page]
            result = ingestion.process_pdf("klasor/rapor.pdf")
        self.assertEqual(set(result), {"filename", "total_pages", "total_chunks", "message"})
        self.assertEqual((result["filename"], result["total_pages"]), ("rapor.pdf", 1))


if __name__ == "__main__":
    unittest.main()
