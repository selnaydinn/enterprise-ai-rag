import importlib
import unittest
from types import SimpleNamespace
from unittest import mock

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter


#Model, DB ve LLM import anında yüklenmiyor (core/config.py); services.ingestion doğrudan import edilebiliyor
def import_ingestion():
    return importlib.import_module("services.ingestion")


def long_page(n, page):
    text = "\n\n".join(f"MADDE {i} - (1) Bu maddede {n} numaralı metnin {i}. paragrafı yer alır. " * 4 for i in range(12))
    return Document(page_content=text, metadata={"page": page})


class SplitPagesTests(unittest.TestCase):

    def setUp(self):
        self.ingestion = import_ingestion()

    #"page" yöntemi önceki process_pdf'teki bölmeyle birebir aynı (bugünkü DB'ler böyle kuruldu)
    def test_page_strategy_matches_previous_splitter(self):
        settings = self.ingestion.get_settings()
        pages = [long_page(1, 0), long_page(2, 1)]
        expected = RecursiveCharacterTextSplitter(chunk_size=settings.chunk_size, chunk_overlap=settings.chunk_overlap).split_documents(pages)
        got = self.ingestion.split_pages(pages, "page")
        self.assertGreater(len(got), 2)
        self.assertEqual([(d.page_content, d.metadata) for d in got], [(d.page_content, d.metadata) for d in expected])

    def test_unknown_strategy_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "madde"):
            self.ingestion.split_pages([long_page(1, 0)], "madde")

    def test_settings_default_is_a_supported_strategy(self):
        self.assertIn(self.ingestion.get_settings().chunking_strategy, self.ingestion.CHUNKING_STRATEGIES)


class PrepareAndProcessTests(unittest.TestCase):

    def setUp(self):
        self.ingestion = import_ingestion()
        patches = [
            mock.patch.object(self.ingestion, "PyPDFLoader"),
            mock.patch.object(self.ingestion, "hash_pdf", return_value="deadbeef"),
            mock.patch.object(self.ingestion.os.path, "exists", return_value=True),
            mock.patch.object(self.ingestion, "get_db"),
        ]
        self.loader = patches[0].start()
        for p in patches[1:3]:
            p.start()
        self.get_db = patches[3].start()
        for p in patches:
            self.addCleanup(p.stop)
        self.loader.return_value.load.return_value = [long_page(1, 0), long_page(2, 4)]

    def prepare(self, **kwargs):
        with self.assertLogs("EnterpriseAI", level="INFO"):
            return self.ingestion.prepare_chunks("klasor/rapor.pdf", **kwargs)

    #DB'ye dokunmadan chunk'lar, id'ler ve metadata hazırlanır (scripts/build_db.py bunu kullanıyor)
    def test_prepare_does_not_touch_the_db(self):
        prepared = self.prepare()
        self.get_db.assert_not_called()
        self.assertEqual((prepared["pdf_name"], prepared["pdf_hash"], prepared["total_pages"]), ("rapor.pdf", "deadbeef", 2))
        self.assertEqual(len(prepared["chunks"]), len(prepared["ids"]))
        first, last = prepared["chunks"][0], prepared["chunks"][-1]
        self.assertEqual(prepared["ids"][0], "h_deadbeef_p1_c0")
        self.assertEqual(prepared["ids"][-1], f"h_deadbeef_p5_c{len(prepared['ids']) - 1}")
        self.assertEqual((first.metadata["source_file"], first.metadata["page_number"], first.metadata["pdf_hash"]), ("rapor.pdf", 1, "deadbeef"))
        self.assertEqual(last.metadata["page_number"], 5)

    def test_strategy_defaults_to_settings(self):
        with mock.patch.object(self.ingestion, "split_pages", wraps=self.ingestion.split_pages) as split:
            self.prepare()
            self.prepare(strategy="page")
        self.assertEqual([c.args[1] for c in split.call_args_list], [self.ingestion.get_settings().chunking_strategy, "page"])

    def test_unknown_strategy_stops_before_the_db(self):
        with self.assertRaises(ValueError):
            self.prepare(strategy="madde")
        self.get_db.assert_not_called()

    #Uygulama db vermiyor: get_db kullanılır
    def test_process_without_db_uses_app_db(self):
        with self.assertLogs("EnterpriseAI", level="INFO"):
            result = self.ingestion.process_pdf("rapor.pdf")
        self.get_db.assert_called_once()
        self.assertEqual(result["total_pages"], 2)
        self.assertEqual(sum(len(c.kwargs["ids"]) for c in self.get_db.return_value.add_documents.call_args_list), result["total_chunks"])

    #Script kendi kurduğu DB'yi veriyor: uygulamanın DB'si açılmaz; chunk'lar 32'şerli paketlerle yazılır
    def test_process_with_given_db_does_not_open_app_db(self):
        #32'den fazla chunk: en az iki paket
        pages = [long_page(n, n) for n in range(12)]
        self.loader.return_value.load.return_value = pages
        db = SimpleNamespace(add_documents=mock.Mock())
        with self.assertLogs("EnterpriseAI", level="INFO"):
            result = self.ingestion.process_pdf("rapor.pdf", db=db, strategy="page")
        self.get_db.assert_not_called()
        sizes = [len(c.kwargs["ids"]) for c in db.add_documents.call_args_list]
        self.assertGreater(result["total_chunks"], 32)
        self.assertEqual(sum(sizes), result["total_chunks"])
        self.assertTrue(all(n == 32 for n in sizes[:-1]))
        self.assertEqual(set(result), {"filename", "total_pages", "total_chunks", "message"})


if __name__ == "__main__":
    unittest.main()
