import importlib
import json
import logging
import re
import threading
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from core.log_setup import (JsonFormatter, NO_REQUEST_ID, REQUEST_ID_HEADER, RequestIdFilter, TEXT_FORMAT,
                            request_id_var, setup_logging)
from tests.test_upload import ANSWER, OPEN_USAGE, bypass_auth

GENERATED_ID = re.compile(r"[0-9a-f]{32}")


def import_main():
    return importlib.import_module("main")


#assertLogs kendi handler'ını kullanıyor (bizim handler'daki filtre çalışmaz); filtre logger'a takılır ki ID kayıt
#anında, isteğin içinde okunsun
def capture_request_ids(testcase):
    logger = logging.getLogger("EnterpriseAI")
    request_filter = RequestIdFilter()
    logger.addFilter(request_filter)
    testcase.addCleanup(logger.removeFilter, request_filter)


#Bir log kaydını filtre + formatter'dan geçirip çıktısını döndürür (handler'ın yaptığı)
def render(formatter, msg="mesaj", exc_info=None):
    record = logging.LogRecord("EnterpriseAI", logging.WARNING, __file__, 1, msg, None, exc_info)
    RequestIdFilter().filter(record)
    return formatter.format(record)


class FormatterTests(unittest.TestCase):

    def test_json_line_has_fields_and_keeps_turkish_characters(self):
        token = request_id_var.set("abc-123")
        self.addCleanup(request_id_var.reset, token)
        data = json.loads(render(JsonFormatter(), "Gemini çağrısı başarısız"))
        self.assertEqual(set(data), {"ts", "level", "logger", "request_id", "msg"})
        self.assertEqual((data["level"], data["logger"], data["request_id"]), ("WARNING", "EnterpriseAI", "abc-123"))
        self.assertIn("çağrısı", render(JsonFormatter(), "Gemini çağrısı başarısız"))

    def test_json_line_includes_traceback(self):
        try:
            raise RuntimeError("patladı")
        except RuntimeError:
            import sys
            line = render(JsonFormatter(), exc_info=sys.exc_info())
        data = json.loads(line)
        self.assertIn("RuntimeError: patladı", data["exc"])
        #Traceback çok satırlı ama JSON tek satır kalıyor
        self.assertNotIn("\n", line)

    def test_outside_request_id_is_dash(self):
        self.assertEqual(json.loads(render(JsonFormatter()))["request_id"], NO_REQUEST_ID)
        self.assertIn(f"[{NO_REQUEST_ID}]", render(logging.Formatter(TEXT_FORMAT)))


class SetupLoggingTests(unittest.TestCase):

    def setUp(self):
        root = logging.getLogger()
        saved = (root.handlers[:], root.level)
        uvicorn_saved = {n: (logging.getLogger(n).handlers[:], logging.getLogger(n).propagate) for n in ("uvicorn", "uvicorn.access")}

        def restore():
            root.handlers, root.level = saved[0], saved[1]
            for n, (handlers, propagate) in uvicorn_saved.items():
                logging.getLogger(n).handlers, logging.getLogger(n).propagate = handlers, propagate
        self.addCleanup(restore)

    #Tekrar çağrılınca handler eklenmiyor, değiştiriliyor (her satır bir kez basılır)
    def test_repeated_setup_keeps_single_handler(self):
        setup_logging("INFO", "text")
        setup_logging("DEBUG", "json")
        root = logging.getLogger()
        self.assertEqual(len(root.handlers), 1)
        self.assertIsInstance(root.handlers[0].formatter, JsonFormatter)
        self.assertEqual(root.level, logging.DEBUG)

    #uvicorn kayıtları kendi handler'ı yerine bizimkine akıyor: aynı biçim ve request ID
    def test_uvicorn_logs_go_through_our_handler(self):
        logging.getLogger("uvicorn.access").addHandler(logging.NullHandler())
        setup_logging("INFO", "text")
        for name in ("uvicorn", "uvicorn.access"):
            with self.subTest(name=name):
                self.assertEqual(logging.getLogger(name).handlers, [])
                self.assertTrue(logging.getLogger(name).propagate)


class RequestIdEndpointTests(unittest.TestCase):

    def setUp(self):
        self.main = import_main()
        bypass_auth(self, self.main)
        self.client = TestClient(self.main.app)
        for patch in (mock.patch.object(self.main, "get_status", return_value=OPEN_USAGE),
                      mock.patch.object(self.main, "record_usage")):
            patch.start()
            self.addCleanup(patch.stop)

    def test_response_has_generated_id(self):
        first = self.client.get("/usage/").headers.get(REQUEST_ID_HEADER)
        second = self.client.get("/usage/").headers.get(REQUEST_ID_HEADER)
        self.assertRegex(first, GENERATED_ID)
        self.assertRegex(second, GENERATED_ID)
        #Her istek kendi ID'sini alıyor; bir önceki isteğin ID'si sızmıyor
        self.assertNotEqual(first, second)

    def test_valid_incoming_id_is_kept(self):
        response = self.client.get("/usage/", headers={REQUEST_ID_HEADER: "frontend-42"})
        self.assertEqual(response.headers.get(REQUEST_ID_HEADER), "frontend-42")

    #Geçersiz ID loga girmez (log injection); yerine yenisi üretilir
    def test_invalid_incoming_id_is_replaced(self):
        for bad in ["a" * 65, "bad id", "x\" sahte=1", "a;b"]:
            with self.subTest(bad=bad):
                response = self.client.get("/usage/", headers={REQUEST_ID_HEADER: bad})
                self.assertRegex(response.headers.get(REQUEST_ID_HEADER), GENERATED_ID)

    #İstek bitince özet satır: yöntem, yol, durum ve süre; ID ile birlikte
    def test_summary_line_is_logged_with_id(self):
        capture_request_ids(self)
        with self.assertLogs("EnterpriseAI", level="INFO") as logs:
            response = self.client.get("/usage/")
        request_id = response.headers.get(REQUEST_ID_HEADER)
        summary = [r for r in logs.records if r.getMessage().startswith("GET /usage/ 200 (")]
        self.assertEqual(len(summary), 1)
        self.assertEqual(summary[0].request_id, request_id)

    #/query/ sync endpoint: thread'de çalışıyor; thread içindeki log da aynı ID'yi taşımalı
    def test_id_reaches_threadpool_logs(self):
        seen = {}

        def fake_prepare(question):
            seen["thread"] = threading.current_thread() is not threading.main_thread()
            seen["id"] = request_id_var.get()
            return ANSWER

        with mock.patch.object(self.main, "prepare_chunks_with_metadata", side_effect=fake_prepare):
            response = self.client.post("/query/", json={"question": "soru"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(seen["thread"])
        self.assertEqual(seen["id"], response.headers.get(REQUEST_ID_HEADER))

    #Tarayıcı başlığı JavaScript'e gösterebilsin (CORS expose) ve frontend gönderebilsin (preflight)
    def test_cors_exposes_and_allows_request_id(self):
        origin = self.main.CORS_ORIGINS[0]
        response = self.client.get("/usage/", headers={"Origin": origin})
        self.assertIn(REQUEST_ID_HEADER.lower(), response.headers.get("access-control-expose-headers", "").lower())
        preflight = self.client.options("/query/", headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": f"Content-Type, {REQUEST_ID_HEADER}",
        })
        self.assertEqual(preflight.status_code, 200)
        self.assertIn(REQUEST_ID_HEADER.lower(), preflight.headers.get("access-control-allow-headers", "").lower())


#Beklenmeyen hata: cevap middleware'in dışında üretiliyor; ID yakalayıcıda elle ekleniyor (main.py)
class UnhandledErrorRequestIdTests(unittest.TestCase):

    def setUp(self):
        self.main = import_main()
        bypass_auth(self, self.main)
        self.client = TestClient(self.main.app, raise_server_exceptions=False)
        patch = mock.patch.object(self.main, "get_status", side_effect=RuntimeError("beklenmeyen"))
        patch.start()
        self.addCleanup(patch.stop)

    def test_500_has_id_and_error_log_carries_same_id(self):
        capture_request_ids(self)
        with self.assertLogs("EnterpriseAI", level="ERROR") as logs:
            response = self.client.get("/usage/")
        self.assertEqual(response.status_code, 500)
        request_id = response.headers.get(REQUEST_ID_HEADER)
        self.assertRegex(request_id, GENERATED_ID)
        error = next(r for r in logs.records if r.getMessage().startswith("Beklenmeyen hata"))
        self.assertEqual(error.request_id, request_id)

    def test_incoming_id_is_kept_on_500(self):
        with self.assertLogs("EnterpriseAI", level="ERROR"):
            response = self.client.get("/usage/", headers={REQUEST_ID_HEADER: "hata-99"})
        self.assertEqual(response.headers.get(REQUEST_ID_HEADER), "hata-99")


if __name__ == "__main__":
    unittest.main()
