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
        with self.assertRaisesRegex(ValueError, "bilinmeyen"):
            self.ingestion.split_pages([long_page(1, 0)], "bilinmeyen")

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
            self.prepare(strategy="bilinmeyen")
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


#Bellekte Chroma yerine geçen küçük DB: process_pdf'in kullandığı add_documents ve geri almanın kullandığı get/delete
#fail_on_call: o sıradaki add_documents çağrısı hata verir (embedding ortasında hata)
#Chroma gibi upsert: aynı id tekrar yazılırsa kayıt çoğalmaz (gerçek davranış geçici bir Chroma koleksiyonunda doğrulandı)
class FakeDb:

    def __init__(self, fail_on_call=None):
        self.records = {}
        self.calls = 0
        self.fail_on_call = fail_on_call

    def add_documents(self, documents, ids):
        self.calls += 1
        if self.calls == self.fail_on_call:
            raise RuntimeError("embedding hatası")
        for doc, chunk_id in zip(documents, ids):
            self.records[chunk_id] = dict(doc.metadata)

    def get(self, where, include=None, limit=None):
        (key, value), = where.items()
        ids = [i for i, meta in self.records.items() if meta.get(key) == value]
        return {"ids": ids[:limit] if limit else ids}

    def delete(self, ids):
        for chunk_id in ids:
            self.records.pop(chunk_id, None)

    def hashes(self):
        return {meta["pdf_hash"] for meta in self.records.values()}


#Yarıda kalan yükleme (notes.md #45): geri alma yalnızca o PDF'in chunk'larını siler, PDF yeniden yüklenebilir
class RollbackTests(unittest.TestCase):

    def setUp(self):
        self.ingestion = import_ingestion()
        self.loader = mock.patch.object(self.ingestion, "PyPDFLoader").start()
        self.hash = mock.patch.object(self.ingestion, "hash_pdf").start()
        mock.patch.object(self.ingestion.os.path, "exists", return_value=True).start()
        self.addCleanup(mock.patch.stopall)
        #32'den fazla chunk: en az iki paket
        self.loader.return_value.load.return_value = [long_page(n, n) for n in range(12)]

    def process(self, db, pdf_hash):
        self.hash.return_value = pdf_hash
        with self.assertLogs("EnterpriseAI", level="INFO"):
            return self.ingestion.process_pdf(f"{pdf_hash}.pdf", db=db, strategy="page")

    def test_rollback_after_failed_batch_removes_only_that_pdf(self):
        db = FakeDb()
        self.process(db, "eskipdf1")
        other = dict(db.records)

        db.fail_on_call = db.calls + 2
        with self.assertRaises(RuntimeError):
            self.process(db, "deadbeef")
        #İlk paket yazıldı, ikincisi hata verdi: geri alınmazsa 32 hayalet chunk kalır
        self.assertEqual(db.hashes(), {"eskipdf1", "deadbeef"})

        self.assertEqual(self.ingestion.delete_chunks_by_hash("deadbeef", db=db), 32)
        self.assertEqual(db.records, other)

        #Duplikasyon kontrolü artık reddetmiyor; aynı PDF baştan yüklenebilir
        with mock.patch.object(self.ingestion, "get_db", return_value=db):
            self.assertFalse(self.ingestion.is_file_already_processed("deadbeef"))
        db.fail_on_call = None
        result = self.process(db, "deadbeef")
        self.assertEqual(len(db.records), len(other) + result["total_chunks"])

    def test_reprocessing_same_pdf_does_not_duplicate(self):
        db = FakeDb()
        first = self.process(db, "deadbeef")
        self.process(db, "deadbeef")
        self.assertEqual(len(db.records), first["total_chunks"])

    def test_nothing_to_roll_back_returns_zero(self):
        db = FakeDb()
        self.assertEqual(self.ingestion.delete_chunks_by_hash("yok", db=db), 0)

    #db verilmezse uygulamanın DB'si; yalnızca id'ler isteniyor
    def test_without_db_uses_app_db(self):
        app_db = mock.Mock()
        app_db.get.return_value = {"ids": ["h_deadbeef_p1_c0"]}
        with mock.patch.object(self.ingestion, "get_db", return_value=app_db):
            self.assertEqual(self.ingestion.delete_chunks_by_hash("deadbeef"), 1)
        app_db.get.assert_called_once_with(where={"pdf_hash": "deadbeef"}, include=[])
        app_db.delete.assert_called_once_with(ids=["h_deadbeef_p1_c0"])


if __name__ == "__main__":
    unittest.main()
