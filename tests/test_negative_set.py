import json
import re
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
NEGATIVE_PATH = PROJECT_ROOT / "eval" / "negative_set.jsonl"
GOLDEN_PATH = PROJECT_ROOT / "eval" / "golden_set.jsonl"
PDF_DIR = PROJECT_ROOT / "uploaded_pdfs"

EXPECTED_COUNT = 40
FIELDS = {"id": str, "topic": str, "kind": str, "question": str, "absent_terms": list, "note": str}
#near_domain: dokümanların konusuna yakın ama cevabı yok (zor); out_of_domain: tamamen kapsam dışı (kolay)
KINDS = {"near_domain", "out_of_domain"}


def read_lines():
    return NEGATIVE_PATH.read_bytes().decode("utf-8").split("\n")


def read_records():
    return [json.loads(line) for line in read_lines() if line.strip()]


#PDF metnindeki kırık boşlukları ("öğr enci") yok saymak için boşlukları siliyoruz
#"İ".lower() "i" + birleşik nokta (U+0307) üretiyor; onu da atıyoruz ki "İstanbul" "istanbul" ile eşleşsin
def normalize(text: str) -> str:
    return re.sub(r"\s+", "", text).lower().replace("̇", "")


class NegativeSetFormatTests(unittest.TestCase):

    def test_file_ends_with_single_newline_and_has_no_blank_lines(self):
        lines = read_lines()
        self.assertEqual(lines[-1], "", "dosya satır sonuyla bitmeli")
        self.assertTrue(all(line.strip() for line in lines[:-1]), "boş satır olmamalı")
        self.assertNotIn("\r", NEGATIVE_PATH.read_bytes().decode("utf-8"))

    def test_has_expected_number_of_records(self):
        self.assertEqual(len(read_records()), EXPECTED_COUNT)

    def test_ids_are_unique_and_sequential(self):
        ids = [r["id"] for r in read_records()]
        self.assertEqual(ids, [f"n{i:02d}" for i in range(1, EXPECTED_COUNT + 1)])

    def test_records_have_exactly_the_schema_fields(self):
        for r in read_records():
            with self.subTest(id=r.get("id")):
                self.assertEqual(set(r), set(FIELDS))
                for field, field_type in FIELDS.items():
                    self.assertIsInstance(r[field], field_type)
                    if field_type is str:
                        self.assertTrue(r[field].strip(), f"'{field}' boş olmamalı")
                self.assertIn(r["kind"], KINDS)
                self.assertTrue(r["absent_terms"])
                self.assertTrue(all(isinstance(t, str) and t.strip() for t in r["absent_terms"]))

    def test_questions_are_unique_and_not_in_golden_set(self):
        questions = [r["question"].strip().lower() for r in read_records()]
        self.assertEqual(len(questions), len(set(questions)))
        golden = {json.loads(line)["question"].strip().lower()
                  for line in GOLDEN_PATH.read_text(encoding="utf-8").splitlines() if line.strip()}
        self.assertFalse(golden & set(questions))


class AbsentTermsTests(unittest.TestCase):

    #Negatif sorunun cevabı hiçbir PDF'te olmamalı; yeni bir PDF eklenip cevap ortaya çıkarsa bu test yakalar
    @classmethod
    def setUpClass(cls):
        try:
            import pypdf
        except ImportError:
            raise unittest.SkipTest("pypdf kurulu değil")
        paths = sorted(PDF_DIR.glob("*.pdf"))
        if not paths:
            raise unittest.SkipTest(f"{PDF_DIR} içinde PDF yok (uploaded_pdfs/ git'e dahil değil)")
        cls.texts = {p.name: normalize(" ".join(page.extract_text() for page in pypdf.PdfReader(p).pages)) for p in paths}

    def test_absent_terms_do_not_appear_in_any_pdf(self):
        for r in read_records():
            for term in r["absent_terms"]:
                with self.subTest(id=r["id"], term=term):
                    found = [name for name, text in self.texts.items() if normalize(term) in text]
                    self.assertEqual(found, [])


if __name__ == "__main__":
    unittest.main()
