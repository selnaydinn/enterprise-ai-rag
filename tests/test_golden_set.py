import hashlib
import importlib.util
import json
import os
import re
import unittest
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[1]
GOLDEN_PATH = PROJECT_ROOT / "eval" / "golden_set.jsonl"
PDF_DIR = PROJECT_ROOT / "uploaded_pdfs"

EXPECTED_COUNT = 25
FIELDS = {"id": str, "topic": str, "question": str, "file": str, "pages": list, "article": str, "note": str}


#Her sorunun cevabını içeren, PDF'den birebir alınmış ifade
#Tek ifade (str): kaydın "pages" alanındaki sayfaların tamamında ve yalnızca onlarda geçmelidir (ör. q09)
#Sayfa başına ifade (dict): aynı kural farklı maddelerde farklı kelimelerle yazılmışsa her sayfanın kendi kanıtı olur;
#her ifade yalnızca kendi sayfasında geçmeli ve dict'in sayfaları "pages" ile birebir aynı olmalıdır (ör. q20)
EVIDENCE = {
    "q01": "özürsüz 10 günü, toplamda 30 günü aşan öğrenciler",
    "q02": "en geç 5 iş günü",
    "q03": "ikinci dönem puanının en az 70",
    "q04": "başarısız ders sayısı en fazla 3 ders olanlar sorumlu olarak sınıflarını geçer",
    "q05": "en fazla bir defa yapılır",
    "q06": "ikiyi geçmemesi esastır",
    "q07": "getirmeyenler için “G”",
    "q08": "ikinci dönemin başlangıcından şubat ayının sonuna kadar haftalık",
    "q09": "Okul değiştirme cezası, ilçe öğrenci disiplin kurulunun",
    "q10": "Kınama cezası için 10",
    "q11": "saati süresi 40 dakikadır",
    "q12": "180 iş gününden az olmaması",
    "q13": "18 yaşını bitirmemiş olma",
    "q14": "bir şubeye alınacak öğrenci sayısı 30",
    "q15": "disiplin cezası almamış olmak ve örnek davranışlara",
    "q16": "60,00 - 69,99 Orta",
    "q17": "sonuçların ilanını takip eden 5 işgünü",
    "q18": "Her iki dersten de en az 70 puan",
    "q19": "on bir ve on ikinci sınıfların yılsonu",
    "q20": {
        42: "kaybolması durumunda yenisi düzenlenmez. Bu durumda öğrenim durum belgesi düzenlenir",  #Madde 69(3)(h)
        44: "bir defaya mahsus olmak üzere öğrenim durum belgesi",  #Madde 73(1)(b)
    },
    "q21": "en az üç öğretim yılının bütün döneminde takdir",
    "q22": "Ayda en az bir kez toplanır",
    "q23": "en geç on iş günü içinde karara bağlar",
    "q24": "Haftada üç gün işletmede beceri eğitimi",
    "q25": "kayıtları okullarında saklı tutulur",
}

#Cevap ifadesinin "pages" dışında da geçtiği ama bilerek doğru kabul edilmeyen sayfalar (çeldirici)
#q08: Aynı nakil takvimi Madde 39(3)'te p26'da birebir tekrarlanıyor; ancak o madde yalnızca yetenek sınavıyla
#öğrenci alan okulları kapsıyor. Soru genel nakli (Madde 38) sorduğu için p26 geçerli kanıt sayılmaz, isabet değildir.
EVIDENCE_ALSO_ON = {
    "q08": [26],
}


def read_lines():
    return GOLDEN_PATH.read_bytes().decode("utf-8").split("\n")


def read_records():
    return [json.loads(line) for line in read_lines() if line.strip()]


#PDF metnindeki kırık boşlukları ("öğr enci") yok saymak için tüm boşlukları siliyoruz
def squash(text: str) -> str:
    return re.sub(r"\s+", "", text)


#eval script'i import edilirken ortam değişkenlerini (HF offline vb.) değiştiriyor; testi etkilemesin diye geri alıyoruz
def load_eval_module():
    saved = dict(os.environ)
    try:
        spec = importlib.util.spec_from_file_location("evaluate_retrieval", PROJECT_ROOT / "eval" / "evaluate_retrieval.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        os.environ.clear()
        os.environ.update(saved)


class GoldenSetFormatTests(unittest.TestCase):

    def test_file_ends_with_single_newline_and_has_no_blank_lines(self):
        lines = read_lines()
        self.assertEqual(lines[-1], "", "dosya satır sonuyla bitmeli")
        self.assertTrue(all(line.strip() for line in lines[:-1]), "boş satır olmamalı")
        self.assertNotIn("\r", GOLDEN_PATH.read_bytes().decode("utf-8"))

    def test_every_line_is_a_json_object(self):
        for n, line in enumerate(read_lines()[:-1], 1):
            with self.subTest(line=n):
                self.assertIsInstance(json.loads(line), dict)

    def test_has_expected_number_of_records(self):
        self.assertEqual(len(read_records()), EXPECTED_COUNT)

    def test_ids_are_unique_and_sequential(self):
        ids = [r["id"] for r in read_records()]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(ids, [f"q{i:02d}" for i in range(1, EXPECTED_COUNT + 1)])

    def test_records_have_exactly_the_schema_fields(self):
        for r in read_records():
            with self.subTest(id=r.get("id")):
                self.assertEqual(set(r), set(FIELDS))
                for field, field_type in FIELDS.items():
                    self.assertIsInstance(r[field], field_type)
                    if field_type is str:
                        self.assertTrue(r[field].strip(), f"'{field}' boş olmamalı")

    def test_pages_are_unique_positive_ints(self):
        for r in read_records():
            with self.subTest(id=r["id"]):
                self.assertTrue(r["pages"])
                #bool da int'in alt sınıfı olduğu için ayrıca eliyoruz
                self.assertTrue(all(type(p) is int and p >= 1 for p in r["pages"]))
                self.assertEqual(len(r["pages"]), len(set(r["pages"])))

    def test_questions_are_unique(self):
        questions = [r["question"].strip().lower() for r in read_records()]
        self.assertEqual(len(questions), len(set(questions)))



class EvalCompatibilityTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.evaluation = load_eval_module()

    def test_load_golden_accepts_the_file(self):
        questions = self.evaluation.load_golden(GOLDEN_PATH)
        self.assertEqual([q["id"] for q in questions], [r["id"] for r in read_records()])

    def test_expected_page_is_counted_as_hit(self):
        #Chroma yerine sahte DB: beklenen dosya/sayfayı 2. sırada döndürüyor
        for item in read_records():
            with self.subTest(id=item["id"]):
                wrong = SimpleNamespace(metadata={"source_file": item["file"], "page_number": max(item["pages"]) + 1})
                right = SimpleNamespace(metadata={"source_file": item["file"], "page_number": item["pages"][0]})
                fake_db = SimpleNamespace(similarity_search_with_relevance_scores=lambda q, k: [(wrong, 0.9), (right, 0.8), (wrong, 0.7)])

                result = self.evaluation.evaluate_question(fake_db, item, k=3)

                self.assertTrue(result["hit_at_3"])
                self.assertEqual(result["first_hit_rank"], 2)

    def test_other_file_is_not_counted_as_hit(self):
        item = read_records()[0]
        other = SimpleNamespace(metadata={"source_file": "baska.pdf", "page_number": item["pages"][0]})
        fake_db = SimpleNamespace(similarity_search_with_relevance_scores=lambda q, k: [(other, 0.9)] * 3)

        self.assertFalse(self.evaluation.evaluate_question(fake_db, item, k=3)["hit_at_3"])


class ReferencePageTests(unittest.TestCase):

    #Sayfa numarası, ingestion'daki gibi PDF'nin 1'den başlayan fiziksel sayfa indeksidir (metadata["page"] + 1)
    @classmethod
    def setUpClass(cls):
        try:
            import pypdf
        except ImportError:
            raise unittest.SkipTest("pypdf kurulu değil")
        cls.pages = {}
        for name in {r["file"] for r in read_records()}:
            path = PDF_DIR / name
            if not path.is_file():
                raise unittest.SkipTest(f"{path} bulunamadı (uploaded_pdfs/ git'e dahil değil)")
            reader = pypdf.PdfReader(path)
            cls.pages[name] = {i + 1: squash(page.extract_text()) for i, page in enumerate(reader.pages)}

    def test_every_record_has_evidence(self):
        self.assertEqual(set(EVIDENCE), {r["id"] for r in read_records()})

    def test_pages_exist_in_pdf(self):
        for r in read_records():
            with self.subTest(id=r["id"]):
                self.assertLessEqual(max(r["pages"]), len(self.pages[r["file"]]))

    def pages_containing(self, file, phrase):
        return sorted(p for p, text in self.pages[file].items() if squash(phrase) in text)

    def test_evidence_appears_exactly_on_reference_pages(self):
        for r in read_records():
            evidence = EVIDENCE[r["id"]]
            extra = EVIDENCE_ALSO_ON.get(r["id"], [])
            if isinstance(evidence, str):
                with self.subTest(id=r["id"]):
                    self.assertEqual(self.pages_containing(r["file"], evidence), sorted(r["pages"] + extra))
                continue
            with self.subTest(id=r["id"], check="kanıt sayfaları = pages"):
                self.assertEqual(sorted(evidence), sorted(r["pages"]))
            for page, phrase in evidence.items():
                with self.subTest(id=r["id"], page=page):
                    self.assertEqual(self.pages_containing(r["file"], phrase), [page])


if __name__ == "__main__":
    unittest.main()
