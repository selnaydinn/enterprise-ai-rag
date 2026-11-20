import importlib
import logging
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from core.log_setup import QuietHealthCheckFilter, is_quiet_health_check

SECRET = "C:\\gizli\\yol chroma hatası"


def import_main():
    return importlib.import_module("main")


#Sağlık kontrolleri (main.py): Docker HEALTHCHECK için, oturumsuz ve içerik bilgisi vermeden
#Auth bypass kullanılmıyor: oturumsuz çalıştıkları burada da doğrulanıyor
class HealthEndpointTests(unittest.TestCase):

    def setUp(self):
        self.main = import_main()
        self.client = TestClient(self.main.app)
        self.get_db = mock.patch.object(self.main, "get_db").start()
        self.addCleanup(mock.patch.stopall)
        self.get_db.return_value.get.return_value = {"ids": ["h_x_p1_c0"]}

    def test_live_is_ok_without_session(self):
        response = self.client.get("/health/live")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
        #DB'ye hiç bakmıyor
        self.get_db.assert_not_called()

    def with_mode(self, mode):
        return mock.patch.object(self.main, "settings", self.main.settings.model_copy(update={"llm_mode": mode}))

    def test_ready_is_ok_without_session_and_reads_one_id(self):
        with self.with_mode("gemini"):
            response = self.client.get("/health/ready")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "llm_mode": "gemini"})
        self.get_db.return_value.get.assert_called_once_with(limit=1, include=[])

    #Yük testi script'i buna bakıp gerçek Gemini'ye yük göndermeyi reddediyor
    def test_ready_shows_fake_mode(self):
        with self.with_mode("fake"):
            response = self.client.get("/health/ready")
        self.assertEqual(response.json(), {"status": "ok", "llm_mode": "fake"})

    #Doküman yüklenmemiş olabilir; boş DB hazır sayılır
    def test_empty_db_is_ready(self):
        self.get_db.return_value.get.return_value = {"ids": []}
        self.assertEqual(self.client.get("/health/ready").status_code, 200)

    #DB okunamazsa 503; ayrıntı kullanıcıya değil yalnızca loga (tür adıyla)
    def test_db_error_returns_503_without_details(self):
        self.get_db.return_value.get.side_effect = RuntimeError(SECRET)
        with self.assertLogs("EnterpriseAI", level="WARNING") as logs:
            response = self.client.get("/health/ready")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"status": "unavailable"})
        self.assertNotIn(SECRET, response.text)
        output = "\n".join(logs.output)
        self.assertIn("RuntimeError", output)
        self.assertNotIn(SECRET, output)

    #Gemini'ye istek atılmıyor (kontrol 30 sn'de bir; kota harcanmamalı)
    def test_ready_does_not_touch_llm(self):
        with mock.patch.object(self.main, "get_llm") as get_llm, \
             mock.patch.object(self.main, "prepare_chunks_with_metadata") as prepare:
            self.client.get("/health/ready")
        get_llm.assert_not_called()
        prepare.assert_not_called()


#Başarılı sağlık kontrolleri loglanmaz; sorun olursa loglanır (core/log_setup.py)
class QuietHealthLogTests(unittest.TestCase):

    def setUp(self):
        self.main = import_main()
        self.client = TestClient(self.main.app)
        self.get_db = mock.patch.object(self.main, "get_db").start()
        self.addCleanup(mock.patch.stopall)
        self.get_db.return_value.get.return_value = {"ids": []}

    def summary_lines(self, logs):
        return [r.getMessage() for r in logs.records if r.getMessage().startswith("GET /health/")]

    def test_successful_check_writes_no_summary_line(self):
        with self.assertLogs("EnterpriseAI", level="INFO") as logs:
            #assertLogs boş kalırsa hata veriyor; karşılaştırma için loglanan bir istek
            self.client.get("/")
            self.client.get("/health/ready")
            self.client.get("/health/live")
        self.assertEqual(self.summary_lines(logs), [])
        self.assertTrue(any(r.getMessage().startswith("GET / 200") for r in logs.records))

    def test_failed_check_is_logged(self):
        self.get_db.return_value.get.side_effect = RuntimeError("db yok")
        with self.assertLogs("EnterpriseAI", level="INFO") as logs:
            self.client.get("/health/ready")
        self.assertEqual(len(self.summary_lines(logs)), 1)
        self.assertTrue(self.summary_lines(logs)[0].startswith("GET /health/ready 503"))

    def test_rule(self):
        self.assertTrue(is_quiet_health_check("/health/ready", 200))
        self.assertTrue(is_quiet_health_check("/health/live", 200))
        self.assertFalse(is_quiet_health_check("/health/ready", 503))
        self.assertFalse(is_quiet_health_check("/documents/", 200))
        #/healthz gibi benzer ama farklı yollar susturulmaz
        self.assertFalse(is_quiet_health_check("/healthz", 200))

    #uvicorn erişim satırı: kayıt argümanları (istemci, yöntem, yol, HTTP sürümü, durum)
    def test_uvicorn_access_filter(self):
        def record(path, status):
            return logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
                                     ("172.18.0.1:5000", "GET", path, "1.1", status), None)
        quiet = QuietHealthCheckFilter()
        self.assertFalse(quiet.filter(record("/health/ready", 200)))
        self.assertTrue(quiet.filter(record("/health/ready", 503)))
        self.assertTrue(quiet.filter(record("/query/", 200)))
        #Beklenmeyen biçimdeki kayıtlar geçer (filtre hiçbir şeyi yanlışlıkla yutmasın)
        self.assertTrue(quiet.filter(logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, "düz mesaj", None, None)))


if __name__ == "__main__":
    unittest.main()
