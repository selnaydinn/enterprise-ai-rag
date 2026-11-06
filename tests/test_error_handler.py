import importlib
import unittest
from unittest import mock

from fastapi.testclient import TestClient

GENERIC_DETAIL = "Beklenmeyen bir hata oluştu."
SECRET = "C:\\gizli\\yol iç hata"


#Model, DB ve LLM import anında yüklenmiyor (core/config.py); main doğrudan import edilebiliyor
def import_main():
    return importlib.import_module("main")


#Hiçbir endpoint'in yakalamadığı hatalar global yakalayıcıya düşüyor (main.py unhandled_exception_handler)
#TestClient varsayılan olarak bu hataları teste geri fırlatıyor; kullanıcının göreceği yanıtı görmek için kapatıyoruz
class UnhandledErrorTests(unittest.TestCase):

    def setUp(self):
        self.main = import_main()
        self.client = TestClient(self.main.app, raise_server_exceptions=False)
        #/usage/'ın try/except'i yok; get_status hatası doğrudan global yakalayıcıya gidiyor
        patch = mock.patch.object(self.main, "get_status", side_effect=RuntimeError(SECRET))
        patch.start()
        self.addCleanup(patch.stop)

    def get_usage(self, headers=None):
        with self.assertLogs("EnterpriseAI", level="ERROR") as logs:
            response = self.client.get("/usage/", headers=headers or {})
        return response, "\n".join(logs.output)

    def test_returns_json_500_without_internal_details(self):
        response, logs = self.get_usage()
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json(), {"detail": GENERIC_DETAIL})
        self.assertNotIn(SECRET, response.text)
        #Detay kullanıcıya gitmiyor ama logda kalıyor; hangi istekte olduğu da yazılıyor
        self.assertIn(SECRET, logs)
        self.assertIn("GET /usage/", logs)

    #Yakalayıcı CORS middleware'inin dışında çalışıyor; başlık elle ekleniyor, yoksa tarayıcı yanıtı engeller
    def test_allowed_origin_gets_cors_header(self):
        #.env'de CORS_ORIGINS tanımlıysa onu kullanıyoruz; varsayılana bağımlı kalmasın
        origin = self.main.CORS_ORIGINS[0]
        response, _ = self.get_usage({"Origin": origin})
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.headers.get("access-control-allow-origin"), origin)
        self.assertIn("Origin", response.headers.get("vary", ""))

    def test_unknown_or_missing_origin_gets_no_cors_header(self):
        for headers in [{"Origin": "http://kotu-site.example"}, {}]:
            with self.subTest(headers=headers):
                response, _ = self.get_usage(headers)
                self.assertEqual(response.status_code, 500)
                self.assertNotIn("access-control-allow-origin", response.headers)

    #/query/'de token sınırı kontrolü try bloğunun dışında; oradaki hata da aynı JSON'u alıyor
    def test_query_usage_check_error_is_handled(self):
        with self.assertLogs("EnterpriseAI", level="ERROR"):
            response = self.client.post("/query/", json={"question": "soru"})
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json(), {"detail": GENERIC_DETAIL})


#Global yakalayıcı HTTPException ve doğrulama hatalarının yerine geçmiyor
class HandledErrorsUnchangedTests(unittest.TestCase):

    def setUp(self):
        self.main = import_main()
        self.client = TestClient(self.main.app, raise_server_exceptions=False)

    def test_unknown_route_is_still_404(self):
        response = self.client.get("/olmayan-adres/")
        self.assertEqual(response.status_code, 404)
        self.assertNotEqual(response.json()["detail"], GENERIC_DETAIL)

    def test_validation_error_is_still_422(self):
        response = self.client.post("/query/", json={"question": ""})
        self.assertEqual(response.status_code, 422)
        self.assertIsInstance(response.json()["detail"], list)

    def test_endpoint_http_exception_keeps_its_message(self):
        response = self.client.post("/upload-pdf/", files={"file": ("a.txt", b"x", "text/plain")})
        self.assertEqual(response.status_code, 400)
        self.assertTrue(response.json()["detail"].startswith("Geçersiz dosya adı."))


if __name__ == "__main__":
    unittest.main()
