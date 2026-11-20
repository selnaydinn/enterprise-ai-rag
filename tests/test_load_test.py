import importlib.util
import json
import unittest
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_script():
    spec = importlib.util.spec_from_file_location("load_test", PROJECT_ROOT / "scripts" / "load_test.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


#Gerçek sunucu yok: httpx.MockTransport istekleri bu fonksiyona yönlendirir (ağa çıkılmaz)
def client_for(handler):
    return httpx.Client(base_url="http://test", transport=httpx.MockTransport(handler))


class PickQuestionsTests(unittest.TestCase):

    def setUp(self):
        self.lt = load_script()

    def test_all_questions_are_mixed_from_both_sets(self):
        questions = self.lt.pick_questions()
        golden = len(self.lt.read_jsonl(self.lt.GOLDEN_PATH))
        negative = len(self.lt.read_jsonl(self.lt.NEGATIVE_PATH))
        self.assertEqual(len(questions), golden + negative)
        #İlk 20'nin hepsi golden olmamalı: iki set karışık
        self.assertEqual({q["kind"] for q in questions[:20]}, {"golden", "negative"})

    #Rastgele değil: her koşuda aynı sorular; golden/negatif oranı korunuyor
    def test_limit_is_proportional_and_deterministic(self):
        first = self.lt.pick_questions(limit=20)
        self.assertEqual(first, self.lt.pick_questions(limit=20))
        self.assertEqual(len(first), 20)
        golden = sum(q["kind"] == "golden" for q in first)
        self.assertTrue(10 <= golden <= 14, golden)
        self.assertEqual(len({q["id"] for q in first}), 20)

    def test_single_set(self):
        self.assertEqual({q["kind"] for q in self.lt.pick_questions("negative", 5)}, {"negative"})


class SummaryTests(unittest.TestCase):

    def setUp(self):
        self.lt = load_script()

    def test_percentile(self):
        values = list(range(1, 101))
        self.assertEqual((self.lt.percentile(values, 50), self.lt.percentile(values, 95)), (50, 95))
        self.assertEqual(self.lt.percentile([7.0], 95), 7.0)
        self.assertIsNone(self.lt.percentile([], 50))

    def test_summary_separates_answers_and_errors(self):
        results = [
            {"http": 200, "status": "success", "seconds": 4.0},
            {"http": 200, "status": "success", "seconds": 6.0},
            {"http": 200, "status": "no_result", "seconds": 2.0},
            {"http": 504, "status": None, "seconds": 40.0},
            {"http": "zaman aşımı", "status": None, "seconds": 180.0},
        ]
        summary = self.lt.summarize(results, wall_seconds=10.0)
        self.assertEqual(summary["http"], {"200": 3, "504": 1, "zaman aşımı": 1})
        self.assertEqual(summary["answer"], {"success": 2, "no_result": 1})
        self.assertEqual(summary["latency"]["success"]["n"], 2)
        self.assertEqual(summary["latency"]["error"]["max"], 180.0)
        self.assertEqual(summary["requests_per_second"], 0.5)
        #Çıktı biçimi hata vermeden kuruluyor
        self.assertIn("Eşzamanlılık 2", self.lt.format_summary(2, summary))

    def test_pause_is_not_counted_as_work(self):
        results = [{"http": 200, "status": "success", "seconds": 5.0}] * 2
        summary = self.lt.summarize(results, wall_seconds=250.0, pause=120)
        self.assertEqual(summary["requests_per_second"], 0.2)


#Emniyet: sahte mod açık değilse test başlamaz
class SafetyTests(unittest.TestCase):

    def setUp(self):
        self.lt = load_script()

    def ready(self, body, status=200):
        return client_for(lambda request: httpx.Response(status, json=body))

    def test_refuses_when_real_gemini(self):
        with self.assertRaises(SystemExit) as raised:
            self.lt.check_ready(self.ready({"status": "ok", "llm_mode": "gemini"}))
        self.assertIn("Sahte LLM modu kapalı", str(raised.exception))

    #Eski imaj (llm_mode alanı yok) da reddedilir
    def test_refuses_when_mode_unknown(self):
        with self.assertRaises(SystemExit):
            self.lt.check_ready(self.ready({"status": "ok"}))

    def test_refuses_when_not_ready(self):
        with self.assertRaises(SystemExit):
            self.lt.check_ready(self.ready({"status": "unavailable"}, status=503))

    def test_accepts_fake_mode(self):
        self.lt.check_ready(self.ready({"status": "ok", "llm_mode": "fake"}))

    #Yanlış parolada bir kez denenir ve durulur (giriş kilidi)
    def test_login_stops_at_first_failure(self):
        calls = []

        def handler(request):
            calls.append(request.url.path)
            return httpx.Response(401, json={"detail": "Parola hatalı."})

        with self.assertRaises(SystemExit) as raised:
            self.lt.login(client_for(handler), "yanlis")
        self.assertEqual(calls, ["/auth/login"])
        self.assertNotIn("yanlis", str(raised.exception))

    def test_login_checks_cookie_is_sent(self):
        def handler(request):
            if request.url.path == "/auth/login":
                return httpx.Response(204, headers={"set-cookie": "session=abc; Path=/; HttpOnly"})
            return httpx.Response(200 if request.headers.get("cookie") == "session=abc" else 401)

        self.lt.login(client_for(handler), "dogru")


class RunLevelTests(unittest.TestCase):

    def setUp(self):
        self.lt = load_script()
        self.questions = [{"id": f"q{i}", "kind": "golden", "question": f"soru {i}"} for i in range(1, 6)]

    def test_all_questions_are_sent_with_request_ids(self):
        seen = []

        def handler(request):
            seen.append((json.loads(request.content)["question"], request.headers["x-request-id"]))
            return httpx.Response(200, json={"status": "success"})

        results, wall = self.lt.run_level(client_for(handler), self.questions, 2, "load-120000", progress=lambda _: None)
        self.assertEqual(len(results), 5)
        self.assertEqual(sorted(q for q, _ in seen), sorted(q["question"] for q in self.questions))
        self.assertIn("load-120000-c2-001", {rid for _, rid in seen})
        #Sonuçlar soru sırasında
        self.assertEqual([r["id"] for r in results], ["q1", "q2", "q3", "q4", "q5"])
        self.assertTrue(all(r["status"] == "success" for r in results))

    def test_errors_are_recorded_not_raised(self):
        def handler(request):
            question = json.loads(request.content)["question"]
            if question == "soru 1":
                raise httpx.ReadTimeout("zaman aşımı")
            if question == "soru 2":
                raise httpx.ConnectError("bağlantı yok")
            if question == "soru 3":
                return httpx.Response(504, json={"detail": "x"})
            return httpx.Response(200, json={"status": "no_result"})

        results, _ = self.lt.run_level(client_for(handler), self.questions, 3, "load-1", progress=lambda _: None)
        self.assertEqual([r["http"] for r in results], ["zaman aşımı", "bağlantı", 504, 200, 200])
        self.assertEqual([r["status"] for r in results], [None, None, None, "no_result", "no_result"])


if __name__ == "__main__":
    unittest.main()
