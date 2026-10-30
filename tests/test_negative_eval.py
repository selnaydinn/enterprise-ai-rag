import importlib.util
import os
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EVAL_DIR = PROJECT_ROOT / "eval"


#eval/evaluate_negative.py "common" ve "evaluate_evidence"i eval/ klasöründen import ediyor ve ortam değişkenlerini
#(HF offline vb.) değiştiriyor; testi etkilemesin diye sys.path, sys.modules ve ortamı geri alıyoruz
def load_negative_eval():
    saved_env, saved_path = dict(os.environ), list(sys.path)
    added = [name for name in ("common", "evaluate_evidence") if name not in sys.modules]
    try:
        sys.path.insert(0, str(EVAL_DIR))
        spec = importlib.util.spec_from_file_location("eval_negative", EVAL_DIR / "evaluate_negative.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        os.environ.clear()
        os.environ.update(saved_env)
        sys.path[:] = saved_path
        for name in added:
            sys.modules.pop(name, None)


def row(group, top_e5, top_rerank, evidence_e5=None, evidence_rerank=None):
    return {"group": group, "top_e5": top_e5, "top_rerank": top_rerank,
            "evidence_e5": evidence_e5, "evidence_rerank": evidence_rerank}


#Eşik taraması saf bir fonksiyon; gerçek model ve DB kullanılmaz
class SweepTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.module = load_negative_eval()

    def setUp(self):
        self.rows = [
            row("golden", 0.85, 6.0, evidence_e5=0.84, evidence_rerank=6.0),
            #Kanıt adaylar arasında değil: her eşikte "kanıt eşik altı" sayılır
            row("golden", 0.80, 2.0),
            #Soru geçse de kanıt parçası eşiğin altında kalabilir
            row("golden", 0.82, 4.0, evidence_e5=0.75, evidence_rerank=-1.0),
            row("near_domain", 0.81, 1.0),
            row("near_domain", 0.78, -3.0),
            row("out_of_domain", 0.74, -6.0),
        ]

    def by_threshold(self, signal, thresholds):
        return {r["threshold"]: r for r in self.module.sweep(self.rows, signal, thresholds)}

    def test_threshold_is_inclusive_and_counts_each_group(self):
        t = self.by_threshold("e5", [0.80])[0.80]
        self.assertEqual(t["near_domain_accepted"], 1)
        self.assertEqual(t["out_of_domain_accepted"], 0)
        self.assertEqual(t["golden_rejected"], 0)
        #Kanıtı 0.75 olan ve kanıtı adaylarda olmayan soru
        self.assertEqual(t["golden_evidence_below"], 2)

    def test_higher_threshold_rejects_more(self):
        t = self.by_threshold("rerank", [-10.0, 3.0, 10.0])
        self.assertEqual((t[-10.0]["near_domain_accepted"], t[-10.0]["out_of_domain_accepted"], t[-10.0]["golden_rejected"]), (2, 1, 0))
        self.assertEqual((t[3.0]["near_domain_accepted"], t[3.0]["golden_rejected"], t[3.0]["golden_evidence_below"]), (0, 1, 2))
        self.assertEqual((t[10.0]["near_domain_accepted"], t[10.0]["golden_rejected"], t[10.0]["golden_evidence_below"]), (0, 3, 3))

    def test_negative_set_loader_rejects_unknown_kind(self):
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "neg.jsonl"
            path.write_text(json.dumps({"id": "n01", "kind": "other", "question": "?"}) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unknown kind"):
                self.module.load_negative(path)

    def test_real_negative_set_loads(self):
        items = self.module.load_negative(EVAL_DIR / "negative_set.jsonl")
        self.assertEqual(len(items), 40)


if __name__ == "__main__":
    unittest.main()
