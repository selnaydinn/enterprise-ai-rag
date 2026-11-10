import importlib
import importlib.util
import json
import re
import unittest
from pathlib import Path

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PDF_DIR = PROJECT_ROOT / "uploaded_pdfs"


def import_chunking():
    return importlib.import_module("services.chunking")


def page(text, number):
    return Document(page_content=text, metadata={"page": number})


def squash(text):
    return re.sub(r"\s+", "", text)


#Yeterli madde başlığı (en az 3, artan numara) olan küçük bir yönetmelik metni
ARTICLES = (
    "ÖRNEK YÖNETMELİĞİ\n"
    "Giriş metni burada bitiyor.\n"
    "Amaç\n"
    "MADDE 1 – (1) Bu Yönetmeliğin amacı örnek kurallar koymaktır.\n"
    "Kapsam\n"
    "MADDE 2 – (1) Bu Yönetmelik bütün okulları kapsar.\n"
    "Tanımlar\n"
    "MADDE 3 – (1) Bu Yönetmelikte geçen terimler tanımlanır.\n"
)


class SplitPagesTests(unittest.TestCase):

    def setUp(self):
        self.chunking = import_chunking()
        self.settings = self.chunking.get_settings()

    def split(self, *pages):
        return self.chunking.split_pages(list(pages), "madde")

    def articles(self, chunks):
        return [c.metadata.get("article") for c in chunks]

    def page_split(self, pages):
        splitter = RecursiveCharacterTextSplitter(chunk_size=self.settings.chunk_size, chunk_overlap=self.settings.chunk_overlap)
        return [(c.page_content, c.metadata) for c in splitter.split_documents(pages)]

    def test_page_strategy_is_unchanged_and_unknown_strategy_is_rejected(self):
        pages = [page(ARTICLES * 5, 0)]
        self.assertEqual([(c.page_content, c.metadata) for c in self.chunking.split_pages(pages, "page")], self.page_split(pages))
        with self.assertRaisesRegex(ValueError, "bilinmeyen"):
            self.chunking.split_pages(pages, "bilinmeyen")

    #Her madde ayrı chunk; başlık satırı ("Kapsam") önceki maddenin sonunda kalmaz, kendi maddesine taşınır
    def test_each_article_gets_its_own_chunk_with_its_title(self):
        chunks = self.split(page(ARTICLES, 4))
        by_article = {c.metadata.get("article"): c.page_content for c in chunks}
        self.assertEqual(self.articles(chunks), [None, "MADDE 1", "MADDE 2", "MADDE 3"])
        self.assertTrue(by_article["MADDE 2"].startswith("Kapsam\nMADDE 2 –"))
        self.assertNotIn("Kapsam", by_article["MADDE 1"])
        #İlk maddeden önceki giriş madde dışı kalır (article yok)
        self.assertIn("Giriş metni", by_article[None])
        self.assertTrue(all(c.metadata["page"] == 4 for c in chunks))

    #Sayfa sınırında da bölünür: devam chunk'ı kendi sayfasını ve başında madde başlığını taşır
    def test_article_continuing_on_next_page_keeps_page_and_gets_heading(self):
        chunks = self.split(page(ARTICLES + "(2) Birinci sayfadaki fıkra.", 0),
                            page("(3) İkinci sayfadaki fıkra.\nSon\nMADDE 4 – (1) Son madde.", 1))
        continued = [c for c in chunks if c.metadata.get("article") == "MADDE 3"]
        self.assertEqual([c.metadata["page"] for c in continued], [0, 1])
        self.assertTrue(continued[1].page_content.startswith("MADDE 3 – Tanımlar\n(3) İkinci sayfadaki fıkra."))
        self.assertNotIn("İkinci sayfadaki", continued[0].page_content)

    def test_heading_variants(self):
        text = ("Amaç\nMadde 1- Birinci.\nKapsam\nMADDE 2 – İkinci.\nEk madde\nMADDE 2/A – Ara madde.\n"
                "Yönlendirme\nMadde 3 \n— \nTire alt satırda.\nGeçiş\nGEÇİCİ MADDE 1 – Geçici.\n"
                "Diğer\nGeçici Madde 2- Küçük harfli geçici.\nEk hüküm\nEK MADDE 1 – Ek madde.\n")
        labels = list(dict.fromkeys(a for a in self.articles(self.split(page(text, 0))) if a))
        self.assertEqual(labels, ["MADDE 1", "MADDE 2", "MADDE 2/A", "MADDE 3", "GEÇİCİ MADDE 1", "GEÇİCİ MADDE 2", "EK MADDE 1"])

    #Metin içindeki atıflar ve satır ortasındaki "Madde" başlık sayılmaz
    def test_references_are_not_headings(self):
        text = ARTICLES + "36 ncı maddenin birinci fıkrası uygulanır.\nBu kural Madde 5 – kapsamında değildir.\n"
        self.assertEqual([a for a in dict.fromkeys(self.articles(self.split(page(text, 0)))) if a], ["MADDE 1", "MADDE 2", "MADDE 3"])

    #Başlığın altındaki değişiklik notu başlık sayılmaz; başlık bir üst satırdan alınır
    def test_amendment_note_is_skipped_for_the_title(self):
        text = ARTICLES + "Kayıt şartları\n(Değişik başlık:RG-13/9/2014-29118)\nMADDE 4 – (1) Kayıt yapılır.\n" + "(2) Uzun fıkra. " * 90
        heading = next(c for c in self.split(page(text, 0)) if c.metadata.get("article") == "MADDE 4" and not c.page_content.startswith("Kayıt"))
        self.assertTrue(heading.page_content.startswith("MADDE 4 – Kayıt şartları\n"))

    #Bölüm başındaki uzun blok (bölüm başlığı, bölüm adı, madde başlığı, aralarında boş satırlar) yeni maddeye taşınır;
    #boş satırlar sınıra sayılırsa blok sığmıyor ve başlık önceki maddede kalıyordu (MEB Madde 8, 43, 64)
    def test_long_title_block_with_blank_lines_moves_to_new_article(self):
        text = ARTICLES + "\nİKİNCİ BÖLÜM\n\nEğitim ve Öğretim Etkinlikleri, Ders Süresi\n\nEğitim etkinlikleri\nMADDE 4 – (1) Etkinlik.\n"
        chunks = self.split(page(text, 0))
        article = next(c.page_content for c in chunks if c.metadata.get("article") == "MADDE 4")
        self.assertTrue(article.startswith("İKİNCİ BÖLÜM"))
        self.assertIn("Eğitim etkinlikleri\nMADDE 4 –", article)
        self.assertNotIn("BÖLÜM", next(c.page_content for c in chunks if c.metadata.get("article") == "MADDE 3"))

    #Başlık bloğunun önündeki satır cümle sonuyla bitmiyorsa (bölünmüş bir cümle olabilir) hiçbir şey taşınmaz
    def test_nothing_is_moved_after_an_unfinished_sentence(self):
        text = ARTICLES + "devam eden bir cümle ve\nOkul Kuralları\nMADDE 4 – (1) Kural.\n"
        chunks = self.split(page(text, 0))
        self.assertIn("Okul Kuralları", next(c.page_content for c in chunks if c.metadata.get("article") == "MADDE 3"))
        self.assertTrue(next(c.page_content for c in chunks if c.metadata.get("article") == "MADDE 4").startswith("MADDE 4 –"))

    #İçeriği yalnızca değişiklik notu olan (mülga) madde kaybolmaz, sonraki maddeye taşınmaz
    def test_repealed_article_is_kept(self):
        text = ARTICLES + "MADDE 4- (Mülga:RG-13/9/2014-29118)\n \nKontenjan\nMADDE 5 – (1) Kontenjan belirlenir.\n"
        chunks = self.split(page(text, 0))
        self.assertEqual([a for a in self.articles(chunks) if a][-2:], ["MADDE 4", "MADDE 5"])
        self.assertEqual(next(c.page_content for c in chunks if c.metadata.get("article") == "MADDE 4"), "MADDE 4- (Mülga:RG-13/9/2014-29118)")

    #Uzun madde fıkra sınırlarından bölünür; her chunk sığar ve başlıkla başlar, yalnızca başlıktan oluşan chunk olmaz
    def test_long_article_is_split_at_paragraphs_with_heading(self):
        paragraphs = "".join(f"({i}) " + "Bu fıkra uzun bir kural anlatır. " * 12 + "\n" for i in range(1, 8))
        chunks = self.split(page(ARTICLES + "Uzun madde\nMADDE 4 – \n" + paragraphs, 0))
        long = [c.page_content for c in chunks if c.metadata.get("article") == "MADDE 4"]
        self.assertGreater(len(long), 1)
        for text in long:
            self.assertLessEqual(len(text), self.settings.chunk_size)
            self.assertTrue(text.startswith("MADDE 4 – Uzun madde\n"), text[:60])
        #İlk chunk fıkrayla başlar; her fıkra bir chunk'ta yer alır (örtüşme yüzünden bazıları iki chunk'ta)
        self.assertTrue(long[0].startswith("MADDE 4 – Uzun madde\n(1)"))
        for i in range(1, 8):
            self.assertTrue(any(f"({i}) " in text for text in long), i)

    #Bölünen maddenin her parçası öncekinin sonuyla başlar (örtüşme); örtüşme tam bir cümleyle başlar ve doludur
    def test_split_article_parts_overlap_from_a_sentence_start(self):
        paragraphs = "".join(f"({i}) " + "Bu fıkra uzun bir kural anlatır. " * 12 + "\n" for i in range(1, 8))
        long = [c.page_content.split("\n", 1)[1] for c in self.split(page(ARTICLES + "Uzun madde\nMADDE 4 – \n" + paragraphs, 0))
                if c.metadata.get("article") == "MADDE 4"]
        for prev, text in zip(long, long[1:]):
            tail = text.split("\n", 1)[0]
            self.assertTrue(prev.rstrip().endswith(tail), tail)
            self.assertGreater(len(tail), 100)
            self.assertLessEqual(len(tail), self.settings.chunk_overlap)
            self.assertTrue(tail.startswith("Bu fıkra"), tail[:30])

    def test_overlap_tail_prefers_sentence_then_word_boundary(self):
        tail = self.chunking._overlap_tail
        self.assertEqual(tail("Birinci cümle burada. İkinci cümle burada.", 30), "İkinci cümle burada.")
        #Cümle başı yoksa kelime sınırından; kelimenin ortasından başlamaz
        self.assertEqual(tail("çok uzun tek bir cümle devam ediyor ve bitmiyor", 20), "ediyor ve bitmiyor")
        self.assertEqual(tail("kısa metin", 300), "kısa metin")
        self.assertEqual(tail("bir metin", 0), "")

    #Fıkrası olmayan ve sığmayan madde karakterle bölünür; yine her chunk başlıkla başlar ve sığar
    def test_article_without_paragraphs_is_split_by_characters(self):
        body = "Uzun bir kural metni devam eder. " * 120
        chunks = self.split(page(ARTICLES + "Görevler\nMADDE 4 –\n\n" + body, 0))
        long = [c.page_content for c in chunks if c.metadata.get("article") == "MADDE 4"]
        self.assertGreater(len(long), 2)
        self.assertTrue(all(t.startswith("MADDE 4 – Görevler\n") and len(t) <= self.settings.chunk_size for t in long))
        self.assertFalse(any(t.strip() == "MADDE 4 – Görevler" for t in long))

    #Başlık sayfa sonuna denk gelirse içeriksiz bölüm sonraki sayfadaki devamıyla birleşir
    def test_heading_at_page_end_is_merged_with_next_page(self):
        chunks = self.split(page(ARTICLES + "Görevler\nMADDE 4 –", 0), page("(1) Görevler sayılır.\nSon\nMADDE 5 – (1) Son.", 1))
        article = [c for c in chunks if c.metadata.get("article") == "MADDE 4"]
        self.assertEqual(len(article), 1)
        self.assertEqual(article[0].metadata["page"], 1)
        self.assertTrue(article[0].page_content.startswith("Görevler\nMADDE 4 –"))
        self.assertIn("(1) Görevler sayılır.", article[0].page_content)

    #İlk "EK" başlığından sonra madde aranmaz: ekler madde dışı, içlerindeki "MADDE 15 –" başlık sayılmaz
    def test_annexes_are_not_split_by_article(self):
        chunks = self.split(page(ARTICLES, 0), page("EK-1 BAŞVURU FORMU\nAlan: değer\nve bu Yönetmeliğin\nMADDE 15 – atfı\n", 1))
        annex = [c for c in chunks if c.metadata["page"] == 1]
        self.assertTrue(annex)
        self.assertTrue(all("article" not in c.metadata for c in annex))
        self.assertIn("MADDE 15 – atfı", annex[0].page_content)

    #Sınır (services/chunking.py ANNEX_HEADING yorumu): "EK" başlığı olmayan tablo son maddenin devamı sayılır
    def test_table_without_annex_heading_belongs_to_last_article(self):
        chunks = self.split(page(ARTICLES, 0), page("TABLO 1 Puan Aralıkları\n85-100 Pekiyi\n70-84 İyi\n", 1))
        table = [c for c in chunks if c.metadata["page"] == 1]
        self.assertEqual([c.metadata.get("article") for c in table], ["MADDE 3"])
        self.assertTrue(table[0].page_content.startswith("MADDE 3 – Tanımlar\nTABLO 1"))

    #Madde yapısı yoksa (az başlık veya sırasız numara) bütün doküman page yöntemiyle bölünür, sonuç birebir aynı
    def test_documents_without_article_structure_fall_back_to_page(self):
        cases = {
            "başlık yok": "Bu bir rapordur.\n\nSonuçlar aşağıdadır.\n" * 40,
            "iki başlık": "Giriş.\nMadde 1 – Bir.\nMadde 2 – İki.\n" + "Rapor metni. " * 200,
            "sırasız numara": "Madde 9 – A.\nMadde 3 – B.\nMadde 7 – C.\nMadde 1 – D.\nMadde 5 – E.\n",
        }
        for name, text in cases.items():
            with self.subTest(name):
                pages = [page(text, 0), page(text, 1)]
                with self.assertLogs("EnterpriseAI", level="INFO") as logs:
                    chunks = self.chunking.split_pages(pages, "madde")
                self.assertEqual([(c.page_content, c.metadata) for c in chunks], self.page_split(pages))
                self.assertIn("page yöntemi kullanıldı", "\n".join(logs.output))


#Gerçek PDF'ler (uploaded_pdfs/ git'te yok; CI'da atlanır)
class RealPdfTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if not PDF_DIR.is_dir() or not list(PDF_DIR.glob("*.pdf")):
            raise unittest.SkipTest(f"{PDF_DIR} bulunamadı (uploaded_pdfs/ git'e dahil değil)")
        ingestion = importlib.import_module("services.ingestion")
        cls.chunks = {}
        with unittest.TestCase().assertLogs("EnterpriseAI", level="INFO"):
            for path in sorted(PDF_DIR.glob("*.pdf")):
                cls.chunks[path.name] = ingestion.prepare_chunks(str(path), "madde")["chunks"]

    #Chunk sırasındaki madde etiketleri; yalnızca art arda tekrarlar birleşir (aynı numaranın sonradan yeniden görünmesi kalır)
    def labels(self, name):
        labels = [c.metadata["article"] for c in self.chunks[name] if c.metadata.get("article")]
        return [label for i, label in enumerate(labels) if i == 0 or label != labels[i - 1]]

    #Ana madde numaraları her dokümanda bir kez, sıralı ve eksiksiz geçer (kaçırılan, yanlış bulunan veya yanlış
    #etiketlenen başlık, ör. "GEÇİCİ MADDE 1"in "MADDE 1" sayılması, bu testte görünür)
    def test_article_numbers_are_complete_and_in_order(self):
        last = {"AcilSaglikHizmetleri.pdf": 41, "isg.pdf": 25, "KanMerkezleriYonetmelik.pdf": 25, "meb_yonetmelik.pdf": 227}
        for name, end in last.items():
            with self.subTest(name):
                nums = [int(m.group(1)) for label in self.labels(name) if (m := re.fullmatch(r"MADDE (\d+)", label))]
                self.assertEqual(nums, list(range(1, end + 1)))

    def test_temporary_articles_keep_their_label(self):
        for name in self.chunks:
            with self.subTest(name):
                self.assertTrue(any(label.startswith("GEÇİCİ MADDE ") for label in self.labels(name)))

    #Kan Merkezleri'nin ekleri (EK-1'den sonrası, s.6-11) madde dışı; ekteki "MADDE 15" atfı başlık sayılmaz.
    #Sınır (services/chunking.py ANNEX_HEADING yorumu): s.6'da EK-1'den önceki değişiklik tablosu MADDE 25'in devamı sayılır
    def test_kan_annexes_are_outside_articles(self):
        chunks = self.chunks["KanMerkezleriYonetmelik.pdf"]
        self.assertTrue(all("article" not in c.metadata for c in chunks if c.metadata["page_number"] >= 7))
        page6 = [c.metadata.get("article") for c in chunks if c.metadata["page_number"] == 6]
        self.assertEqual(page6[0], "MADDE 25")
        self.assertIsNone(page6[-1])
        self.assertEqual(self.labels("KanMerkezleriYonetmelik.pdf").count("MADDE 15"), 1)

    def test_no_chunk_exceeds_the_size_or_is_heading_only(self):
        size = importlib.import_module("core.settings").get_settings().chunk_size
        for name, chunks in self.chunks.items():
            with self.subTest(name):
                self.assertTrue(all(len(c.page_content) <= size for c in chunks))
                self.assertFalse([c.page_content for c in chunks
                                  if re.fullmatch(r"(?s)([^\n]*\n)?\s*(GEÇİCİ |EK )?MADDE \d+(/[A-Z])?\s*[-–—]\s*", c.page_content)])

    #Golden set'in her kanıt cümlesi tek bir chunk'ın içinde ve beklenen sayfada (eval'in kanıt eşleşmesi buna dayanıyor)
    def test_golden_evidence_stays_in_one_chunk_on_its_page(self):
        spec = importlib.util.spec_from_file_location("golden_tests", PROJECT_ROOT / "tests" / "test_golden_set.py")
        golden = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(golden)
        for line in (PROJECT_ROOT / "eval" / "golden_set.jsonl").read_text(encoding="utf-8").splitlines():
            item = json.loads(line)
            evidence = golden.EVIDENCE[item["id"]]
            for expected_page, phrase in (evidence.items() if isinstance(evidence, dict) else [(None, evidence)]):
                with self.subTest(id=item["id"]):
                    pages = {c.metadata["page_number"] for c in self.chunks[item["file"]] if squash(phrase) in squash(c.page_content)}
                    self.assertTrue(pages & set([expected_page] if expected_page else item["pages"]), pages)


if __name__ == "__main__":
    unittest.main()
