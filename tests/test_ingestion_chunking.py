import importlib
import shutil
import tempfile
import unittest
from pathlib import Path
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

    #Her parça PDF'in toplam parça sayısını taşır; yarım kalan yükleme DB'deki sayıyla karşılaştırılarak bulunur
    def test_every_chunk_carries_chunk_total(self):
        prepared = self.prepare()
        total = len(prepared["chunks"])
        self.assertGreater(total, 1)
        self.assertEqual({c.metadata["chunk_total"] for c in prepared["chunks"]}, {total})

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

    #where verilmezse bütün kayıtlar (açılış kontrolü bütün metadata'yı okuyor)
    def get(self, where=None, include=None, limit=None):
        if where:
            (key, value), = where.items()
            ids = [i for i, meta in self.records.items() if meta.get(key) == value]
        else:
            ids = list(self.records)
        ids = ids[:limit] if limit else ids
        result = {"ids": ids}
        if include and "metadatas" in include:
            result["metadatas"] = [dict(self.records[i]) for i in ids]
        return result

    def delete(self, ids):
        for chunk_id in ids:
            self.records.pop(chunk_id, None)

    def hashes(self):
        return {meta["pdf_hash"] for meta in self.records.values()}


#Gerçek process_pdf ile sahte DB'ye yazan testlerin ortak hazırlığı (PDF okuma ve hash sahte)
class ProcessIntoFakeDb(unittest.TestCase):

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


#Yarıda kalan yükleme (notes.md #45): geri alma yalnızca o PDF'in chunk'larını siler, PDF yeniden yüklenebilir
class RollbackTests(ProcessIntoFakeDb):

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
        self.assertEqual(self.ingestion.document_status("deadbeef", db=db)["state"], "missing")
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


#PDF'in DB'deki durumu: yarım kalan yükleme, chunk_total ile parça sayısı karşılaştırılarak bulunur
#(notes2.md Faz 2, sahipsiz parçalar). PDF'ler process_pdf ile sahte DB'ye gerçekten yazılıyor
class DocumentStatusTests(ProcessIntoFakeDb):

    def test_missing(self):
        self.assertEqual(self.ingestion.document_status("deadbeef", db=FakeDb()),
                         {"state": "missing", "found": 0, "total": None})

    def test_complete_after_full_upload(self):
        db = FakeDb()
        result = self.process(db, "deadbeef")
        total = result["total_chunks"]
        self.assertEqual(self.ingestion.document_status("deadbeef", db=db),
                         {"state": "complete", "found": total, "total": total})

    #Uygulama ikinci paketten önce öldürülmüş gibi: ilk 32 parça DB'de kaldı, geri alma çalışmadı
    def test_incomplete_when_upload_stopped_midway(self):
        db = FakeDb(fail_on_call=2)
        with self.assertRaises(RuntimeError):
            self.process(db, "deadbeef")
        status = self.ingestion.document_status("deadbeef", db=db)
        self.assertEqual(status["state"], "incomplete")
        self.assertEqual(status["found"], 32)
        self.assertGreater(status["total"], 32)

    #chunk_total alanından önce yüklenmiş parçalar: tam mı bilinemez
    def test_legacy_chunks_without_total(self):
        db = FakeDb()
        self.process(db, "deadbeef")
        for meta in db.records.values():
            meta.pop("chunk_total")
        status = self.ingestion.document_status("deadbeef", db=db)
        self.assertEqual(status["state"], "legacy")
        self.assertIsNone(status["total"])

    #Başka PDF'lerin parçaları sayılmaz
    def test_other_pdfs_are_not_counted(self):
        db = FakeDb()
        self.process(db, "eskipdf1")
        self.assertEqual(self.ingestion.document_status("deadbeef", db=db)["state"], "missing")

    #db verilmezse uygulamanın DB'si
    def test_without_db_uses_app_db(self):
        app_db = mock.Mock()
        app_db.get.return_value = {"ids": ["a"], "metadatas": [{"pdf_hash": "deadbeef", "chunk_total": 1}]}
        with mock.patch.object(self.ingestion, "get_db", return_value=app_db):
            self.assertEqual(self.ingestion.document_status("deadbeef")["state"], "complete")
        app_db.get.assert_called_once_with(where={"pdf_hash": "deadbeef"}, include=["metadatas"])


#Açılış kontrolü (main.py lifespan): yarım kalmış yükleme geri alınır, diğer tutarsızlıklar yalnızca raporlanır
#Gerçek dosyalar geçici klasörde, DB sahte; hash'ler dosya içeriğinden (hash_bytes) gerçekten hesaplanıyor
class CheckDocumentsTests(unittest.TestCase):

    def setUp(self):
        self.ingestion = import_ingestion()
        self.db = FakeDb()
        self.dir = Path(tempfile.mkdtemp(prefix="check_docs_"))
        self.addCleanup(shutil.rmtree, self.dir, True)

    #Diskte PDF dosyası; içeriği (dolayısıyla hash'i) ada göre farklı
    def write(self, name, content=None):
        content = content or f"%PDF-1.4 {name}".encode()
        (self.dir / name).write_bytes(content)
        return self.ingestion.hash_bytes(content)

    #DB'ye found adet parça; total None ise chunk_total yok (eski kayıt)
    def add_chunks(self, name, pdf_hash, found, total):
        for i in range(found):
            meta = {"source_file": name, "page_number": 1, "pdf_hash": pdf_hash}
            if total is not None:
                meta["chunk_total"] = total
            self.db.records[f"h_{pdf_hash}_p1_c{i}"] = meta

    def check(self):
        with self.assertLogs("EnterpriseAI", level="INFO") as logs:
            report = self.ingestion.check_documents(str(self.dir), db=self.db)
        return report, "\n".join(logs.output)

    def files(self):
        return sorted(p.name for p in self.dir.iterdir())

    def test_clean_state_logs_one_summary_line(self):
        self.add_chunks("tam.pdf", self.write("tam.pdf"), 48, 48)
        self.add_chunks("eski.pdf", self.write("eski.pdf"), 10, None)
        report, output = self.check()
        self.assertNotIn("WARNING", output)
        self.assertIn("Doküman kontrolü: 2 doküman (1 tam, 1 eski kayıt); sorun yok.", output)
        self.assertEqual(len(self.db.records), 58)
        self.assertEqual(self.files(), ["eski.pdf", "tam.pdf"])

    #Asıl durum: yükleme sırasında uygulama öldürüldü, 32/48 parça ve dosya kaldı
    def test_incomplete_upload_is_rolled_back_with_its_file(self):
        self.add_chunks("tam.pdf", self.write("tam.pdf"), 48, 48)
        half = self.write("yarim.pdf")
        self.add_chunks("yarim.pdf", half, 32, 48)
        report, output = self.check()
        self.assertIn("Yarım kalmış yükleme geri alındı: yarim.pdf (32/48 parça silindi, dosya kaldırıldı)", output)
        self.assertEqual(self.db.hashes(), {self.ingestion.hash_bytes(b"%PDF-1.4 tam.pdf")})
        self.assertEqual(self.files(), ["tam.pdf"])

    def test_incomplete_upload_without_file_is_still_rolled_back(self):
        self.add_chunks("yarim.pdf", "deadbeef", 32, 48)
        _, output = self.check()
        self.assertIn("yarim.pdf (32/48 parça silindi, dosyası yoktu)", output)
        self.assertEqual(self.db.records, {})

    #Aynı ad ama farklı içerik (dosya elle değiştirilmiş): yarım parçalar silinir, dosyaya dokunulmaz
    def test_file_with_same_name_but_other_content_is_kept(self):
        self.add_chunks("rapor.pdf", "deadbeef", 32, 48)
        self.write("rapor.pdf", b"%PDF-1.4 baska icerik")
        _, output = self.check()
        self.assertEqual(self.db.records, {})
        self.assertEqual(self.files(), ["rapor.pdf"])
        self.assertIn("DB'de parçası olmayan PDF: rapor.pdf", output)

    #Kesin olmayan durumlar silinmez, yalnızca raporlanır
    def test_uncertain_cases_are_only_reported(self):
        self.add_chunks("kayip.pdf", "deadbeef", 48, 48)
        self.add_chunks("eski_kayip.pdf", "cafebabe", 10, None)
        self.write("fazla.pdf")
        report, output = self.check()
        self.assertEqual(len(self.db.records), 58)
        self.assertEqual(self.files(), ["fazla.pdf"])
        self.assertIn("Dosyası olmayan doküman: kayip.pdf (48 parça DB'de", output)
        self.assertIn("Dosyası olmayan doküman: eski_kayip.pdf (10 parça DB'de", output)
        self.assertIn("DB'de parçası olmayan PDF: fazla.pdf", output)
        self.assertIn("; 3 sorun (yukarıda).", output)
        self.assertEqual(report["files_without_chunks"], ["fazla.pdf"])

    #inspect yalnızca bakar
    def test_inspect_changes_nothing(self):
        half = self.write("yarim.pdf")
        self.add_chunks("yarim.pdf", half, 32, 48)
        report = self.ingestion.inspect_documents(str(self.dir), db=self.db)
        self.assertEqual(len(self.db.records), 32)
        self.assertEqual(self.files(), ["yarim.pdf"])
        (doc,) = report["documents"]
        self.assertEqual((doc["state"], doc["found"], doc["total"], doc["on_disk"]), ("incomplete", 32, 48, ["yarim.pdf"]))

    def test_missing_upload_dir_is_not_an_error(self):
        self.add_chunks("tam.pdf", "deadbeef", 48, 48)
        with self.assertLogs("EnterpriseAI", level="INFO"):
            report = self.ingestion.check_documents(str(self.dir / "yok"), db=self.db)
        self.assertEqual(report["files_without_chunks"], [])


if __name__ == "__main__":
    unittest.main()
