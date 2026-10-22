import contextlib
import importlib
import io
import re
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PDF_PATH = PROJECT_ROOT / "uploaded_pdfs" / "meb_yonetmelik.pdf"

#Boşluk içeren boş satır: "\n" + yalnızca boşluk/tab + "\n"
SPACED_BLANK = re.compile(r"\n[^\S\n]+\n")


_ingestion = None


#services.ingestion import edilirken core.config modeli, DB'yi ve Gemini'yi yüklüyor; bir kez, sahte config ile import ediyoruz
#(Yalnızca core.config girdisini geri alıyoruz; tüm sys.modules'ü geri almak numpy gibi modülleri bozar)
def import_ingestion():
    global _ingestion
    if _ingestion is None:
        missing = object()
        saved = {name: sys.modules.get(name, missing) for name in ("core.config", "services.ingestion")}
        sys.modules["core.config"] = SimpleNamespace(db=None)
        sys.modules.pop("services.ingestion", None)
        try:
            _ingestion = importlib.import_module("services.ingestion")
        finally:
            for name, module in saved.items():
                if module is missing:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = module
    return _ingestion


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

        with mock.patch.object(ingestion, "db", fake_db), \
             mock.patch.object(ingestion, "PyPDFLoader") as loader, \
             mock.patch.object(ingestion.RecursiveCharacterTextSplitter, "split_documents", side_effect=fake_split), \
             mock.patch.object(ingestion, "hash_pdf", return_value="deadbeef"), \
             mock.patch.object(ingestion.os.path, "exists", return_value=True):
            loader.return_value.load.return_value = [page]
            #process_pdf emoji'li print'ler içeriyor; Windows konsolunda (cp1252) hata vermesin diye çıktıyı yutuyoruz
            with contextlib.redirect_stdout(io.StringIO()):
                ingestion.process_pdf("sahte.pdf")

        self.assertEqual(captured["texts"], ["MADDE 1 - (1) Amaç \n\n(2) Kapsam"])
        fake_db.add_documents.assert_not_called()


if __name__ == "__main__":
    unittest.main()
