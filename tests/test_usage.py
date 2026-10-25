import shutil
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from services import usage
from tests.test_upload import import_main

LIMIT = 1000


def at(hour, minute=0, day=24):
    return datetime(2026, 10, day, hour, minute, tzinfo=usage.TURKEY_TZ)


#Gerçek usage.db yerine geçici bir dosya; sınır testlerde küçük tutuluyor
#Pencere 6 saate sabitleniyor; .env'deki TOKEN_WINDOW_HOURS testleri etkilemesin
class TempUsageDb(unittest.TestCase):

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="usage_test_"))
        patches = [
            mock.patch.object(usage, "USAGE_DB_PATH", self.root / "usage.db"),
            mock.patch.object(usage, "TOKEN_LIMIT", LIMIT),
            mock.patch.object(usage, "WINDOW_HOURS", 6),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(shutil.rmtree, self.root, True)


class UsageTests(TempUsageDb):

    def test_window_follows_turkey_time(self):
        self.assertEqual(usage.window_bounds(at(13, 45)), (at(12), at(18)))
        self.assertEqual(usage.window_bounds(at(5, 59)), (at(0), at(6)))
        self.assertEqual(usage.window_bounds(at(18)), (at(18), at(0, day=25)))
        #UTC 21:30, Türkiye'de ertesi gün 00:30
        self.assertEqual(usage.window_bounds(datetime(2026, 10, 24, 21, 30, tzinfo=timezone.utc)),
                         (at(0, day=25), at(6, day=25)))

    def test_empty_window_starts_at_zero(self):
        status = usage.get_status(at(12))
        self.assertEqual(status, {"used": 0, "limit": LIMIT, "resets_at": "2026-10-24T18:00:00+03:00", "limited": False})

    def test_usage_accumulates_in_same_window(self):
        usage.record_usage(300, at(12, 10))
        usage.record_usage(200, at(17, 59))
        self.assertEqual(usage.get_status(at(17, 59))["used"], 500)

    def test_new_window_starts_from_zero(self):
        usage.record_usage(LIMIT, at(17, 59))
        self.assertTrue(usage.get_status(at(17, 59))["limited"])
        status = usage.get_status(at(18))
        self.assertEqual(status["used"], 0)
        self.assertFalse(status["limited"])
        self.assertEqual(status["resets_at"], "2026-10-25T00:00:00+03:00")

    def test_last_request_may_exceed_limit_without_clipping(self):
        usage.record_usage(900, at(12))
        usage.record_usage(300, at(12))
        status = usage.get_status(at(12))
        self.assertEqual(status["used"], 1200)
        self.assertTrue(status["limited"])

    def test_zero_tokens_are_not_recorded(self):
        usage.record_usage(0, at(12))
        self.assertEqual(usage.get_status(at(12))["used"], 0)

    def test_concurrent_records_are_not_lost(self):
        def worker():
            for _ in range(20):
                usage.record_usage(1, at(12))
        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(usage.get_status(at(12))["used"], 200)


class QueryUsageEndpointTests(TempUsageDb):

    def setUp(self):
        super().setUp()
        self.main = import_main()
        self.client = TestClient(self.main.app)
        patch = mock.patch.object(self.main, "prepare_chunks_with_metadata",
                                  return_value={"status": "Success", "token_usage": 300})
        self.prepare = patch.start()
        self.addCleanup(patch.stop)

    def ask(self):
        return self.client.post("/query/", json={"question": "soru"})

    def test_query_records_real_usage(self):
        response = self.ask()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["usage"]["used"], 300)
        #Eski total_token_usage alanı kaldırıldı; frontend sadece usage'ı okuyor
        self.assertNotIn("total_token_usage", response.json())

    def test_limit_reached_returns_429_without_calling_llm(self):
        usage.record_usage(LIMIT)
        response = self.ask()
        self.assertEqual(response.status_code, 429)
        reset_time = datetime.fromisoformat(usage.get_status()["resets_at"]).strftime("%H:%M")
        self.assertEqual(response.json()["detail"], f"Token kullanım sınırına ulaşıldı. Sınırın sıfırlanma saati: {reset_time}.")
        self.prepare.assert_not_called()

    def test_last_request_may_exceed_limit_then_locks(self):
        usage.record_usage(900)
        response = self.ask()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["usage"]["used"], 1200)
        self.assertTrue(response.json()["usage"]["limited"])
        self.assertEqual(self.ask().status_code, 429)
        self.prepare.assert_called_once()

    def test_usage_endpoint(self):
        usage.record_usage(250)
        response = self.client.get("/usage/")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["used"], 250)
        self.assertEqual(body["limit"], LIMIT)
        self.assertFalse(body["limited"])
        self.assertIn("resets_at", body)


if __name__ == "__main__":
    unittest.main()
