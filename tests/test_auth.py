import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from core import auth
from tests.test_settings import make_settings
from tests.test_upload import PDF, import_main

PASSWORD = "dogru-parola"
SECRET = "s" * 48

#Oturum olmadan erişilebilen endpoint'ler; bunun dışındaki her endpoint 401 dönmeli (RouteScanTests)
PUBLIC_ROUTES = {("GET", "/"), ("POST", "/auth/login"), ("POST", "/auth/logout")}


#Auth ayarları testte sabit: gerçek .env okunmuyor (make_settings); core.auth ve main aynı ayar nesnesini görüyor
class AuthSettingsMixin:

    def use_settings(self, **env):
        values = {"APP_PASSWORD": PASSWORD, "SESSION_SECRET": SECRET, **env}
        self.settings = make_settings(**values)
        patch = mock.patch.object(auth, "get_settings", return_value=self.settings)
        patch.start()
        self.addCleanup(patch.stop)
        return self.settings


# BİRİM TESTLERİ (core/auth.py)

class TokenTests(AuthSettingsMixin, unittest.TestCase):

    def setUp(self):
        self.use_settings()

    def test_new_token_is_valid(self):
        self.assertTrue(auth.verify_session_token(auth.create_session_token()))

    def test_token_expires_after_session_hours(self):
        token = auth.create_session_token(now=1000)
        self.assertTrue(auth.verify_session_token(token, now=1000 + 12 * 3600 - 1))
        self.assertFalse(auth.verify_session_token(token, now=1000 + 12 * 3600))

    def test_changed_expiry_breaks_signature(self):
        #Bitiş zamanını ileri almak (oturumu uzatmak) imzayı bozar
        payload, signature = auth.create_session_token().split(".")
        self.assertFalse(auth.verify_session_token(f"{int(payload) + 3600}.{signature}"))

    def test_changed_signature_is_rejected(self):
        payload, signature = auth.create_session_token().split(".")
        forged = signature[:-1] + ("0" if signature[-1] != "0" else "1")
        self.assertFalse(auth.verify_session_token(f"{payload}.{forged}"))

    def test_malformed_tokens_are_rejected(self):
        for token in ["", "abc", "123", "123.", ".abc", "abc.def", "²³.abc", "-5.abc"]:
            with self.subTest(token=token):
                self.assertFalse(auth.verify_session_token(token))

    def test_token_from_another_secret_is_rejected(self):
        #SESSION_SECRET değişince eski oturumlar geçersiz olur (acil durumda tüm oturumları kapatma yolu)
        token = auth.create_session_token()
        self.use_settings(SESSION_SECRET="b" * 48)
        self.assertFalse(auth.verify_session_token(token))


class PasswordTests(AuthSettingsMixin, unittest.TestCase):

    def setUp(self):
        self.use_settings()

    def test_only_exact_password_matches(self):
        self.assertTrue(auth.check_password(PASSWORD))
        for wrong in ["", "dogru", PASSWORD + "x", PASSWORD.upper(), " " + PASSWORD]:
            with self.subTest(wrong=wrong):
                self.assertFalse(auth.check_password(wrong))


class ConfigCheckTests(AuthSettingsMixin, unittest.TestCase):

    def test_missing_values_stop_startup_without_leaking_values(self):
        for env, missing in [({"APP_PASSWORD": ""}, "APP_PASSWORD"), ({"SESSION_SECRET": ""}, "SESSION_SECRET")]:
            with self.subTest(missing=missing):
                self.use_settings(**env)
                with self.assertRaises(RuntimeError) as ctx:
                    auth.check_auth_config()
                self.assertIn(missing, str(ctx.exception))
                self.assertNotIn(PASSWORD, str(ctx.exception))
                self.assertNotIn(SECRET, str(ctx.exception))

    def test_complete_config_passes(self):
        self.use_settings()
        auth.check_auth_config()

    def test_disabled_auth_needs_no_values_but_warns(self):
        self.use_settings(AUTH_ENABLED="false", APP_PASSWORD="", SESSION_SECRET="")
        with self.assertLogs("EnterpriseAI", level="WARNING"):
            auth.check_auth_config()


class LoginGuardTests(unittest.TestCase):

    #Sahte saat: 15 dakika beklemeden kilidin bitişi test edilebiliyor
    def setUp(self):
        self.now = 0.0
        self.guard = auth.LoginGuard(max_failures=5, lockout_seconds=900, clock=lambda: self.now)

    def fail(self, client="a", times=1):
        for _ in range(times):
            self.guard.record_failure(client)

    def test_locks_after_max_failures(self):
        self.fail(times=4)
        self.assertEqual(self.guard.locked_seconds("a"), 0)
        with self.assertLogs("EnterpriseAI", level="WARNING"):
            self.fail()
        self.assertEqual(self.guard.locked_seconds("a"), 900)

    def test_lock_expires(self):
        with self.assertLogs("EnterpriseAI", level="WARNING"):
            self.fail(times=5)
        self.now = 899
        self.assertEqual(self.guard.locked_seconds("a"), 1)
        self.now = 900
        self.assertEqual(self.guard.locked_seconds("a"), 0)

    def test_success_resets_counter(self):
        self.fail(times=4)
        self.guard.record_success("a")
        self.fail(times=4)
        self.assertEqual(self.guard.locked_seconds("a"), 0)

    def test_old_failures_are_forgotten(self):
        #Dünkü 4 hata bugünkü 1 hatayla kilitlememeli
        self.fail(times=4)
        self.now = 901
        self.fail()
        self.assertEqual(self.guard.locked_seconds("a"), 0)

    def test_clients_are_counted_separately(self):
        #Başkasının hatalı denemeleri sizi kilitlemez
        with self.assertLogs("EnterpriseAI", level="WARNING"):
            self.fail("saldirgan", times=5)
        self.assertGreater(self.guard.locked_seconds("saldirgan"), 0)
        self.assertEqual(self.guard.locked_seconds("siz"), 0)

    def test_tracked_clients_are_pruned(self):
        self.guard.MAX_TRACKED_CLIENTS = 3
        for client in ["a", "b", "c"]:
            self.fail(client)
        self.now = 1000
        self.fail("d")
        self.assertEqual(set(self.guard._clients), {"d"})


# ENDPOINT TESTLERİ (main.py)
#Gerçek require_auth çalışıyor (bypass_auth yok); model, DB ve Gemini'ye hiç ulaşılmıyor: 401 endpoint'ten önce dönüyor

class EndpointAuthTests(AuthSettingsMixin, unittest.TestCase):

    def setUp(self):
        self.main = import_main()
        settings = self.use_settings()
        patch = mock.patch.object(self.main, "settings", settings)
        patch.start()
        self.addCleanup(patch.stop)
        auth.login_guard.reset()
        self.addCleanup(auth.login_guard.reset)
        self.client = TestClient(self.main.app)

    def login(self, password=PASSWORD):
        return self.client.post("/auth/login", json={"password": password})

    def test_me_without_cookie_is_401(self):
        response = self.client.get("/auth/me")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "Oturum açmanız gerekiyor.")

    def test_login_sets_secure_cookie_and_opens_session(self):
        response = self.login()
        self.assertEqual(response.status_code, 204)
        cookie = response.headers["set-cookie"]
        self.assertTrue(cookie.startswith(f"{auth.SESSION_COOKIE}="))
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=lax", cookie)
        self.assertIn(f"Max-Age={12 * 3600}", cookie)
        #COOKIE_SECURE=false (yerel): Secure yok
        self.assertNotIn("Secure", cookie)
        self.assertEqual(self.client.get("/auth/me").json(), {"authenticated": True})

    def test_cookie_secure_setting_adds_secure_flag(self):
        self.use_settings(COOKIE_SECURE="true")
        self.assertIn("Secure", self.login().headers["set-cookie"])

    def test_wrong_password_is_401_without_cookie_and_not_logged(self):
        with self.assertLogs("EnterpriseAI", level="WARNING") as logs:
            response = self.login("yanlis-parola")
        self.assertEqual(response.status_code, 401)
        self.assertNotIn("set-cookie", response.headers)
        self.assertNotIn("yanlis-parola", "\n".join(logs.output))

    def test_empty_password_is_422(self):
        self.assertEqual(self.login("").status_code, 422)

    def test_forged_or_expired_cookie_is_401(self):
        expired = auth.create_session_token(now=time.time() - 13 * 3600)
        for token in ["123.abc", expired]:
            with self.subTest(token=token):
                self.client.cookies.set(auth.SESSION_COOKIE, token)
                self.assertEqual(self.client.get("/auth/me").status_code, 401)

    def test_logout_ends_session(self):
        self.login()
        self.assertEqual(self.client.post("/auth/logout").status_code, 204)
        self.assertEqual(self.client.get("/auth/me").status_code, 401)

    def test_lockout_after_five_failures_even_with_right_password(self):
        with self.assertLogs("EnterpriseAI", level="WARNING"):
            for _ in range(5):
                self.assertEqual(self.login("yanlis").status_code, 401)
        response = self.login()
        self.assertEqual(response.status_code, 429)
        self.assertIn("15 dakika", response.json()["detail"])
        self.assertNotIn("set-cookie", response.headers)

    def test_disabled_auth_lets_requests_through(self):
        self.use_settings(AUTH_ENABLED="false")
        self.assertEqual(self.client.get("/auth/me").status_code, 200)

    def test_startup_fails_when_password_missing(self):
        #Lifespan modelleri yüklemeden önce auth ayarlarını kontrol ediyor
        self.use_settings(APP_PASSWORD="")
        with mock.patch.object(self.main, "get_llm") as get_llm, self.assertRaises(RuntimeError):
            with TestClient(self.main.app):
                pass
        get_llm.assert_not_called()


class StaticPdfTests(AuthSettingsMixin, unittest.TestCase):

    def setUp(self):
        self.main = import_main()
        #main.login AUTH_ENABLED'ı main.settings'ten okuyor; yerel .env'deki değer testi etkilemesin
        patch = mock.patch.object(self.main, "settings", self.use_settings())
        patch.start()
        self.addCleanup(patch.stop)
        self.upload_dir = Path(tempfile.mkdtemp(prefix="pdf_test_"))
        self.addCleanup(shutil.rmtree, self.upload_dir, True)
        (self.upload_dir / "rapor.pdf").write_bytes(PDF)
        patch = mock.patch.object(self.main, "UPLOAD_DIR", str(self.upload_dir))
        patch.start()
        self.addCleanup(patch.stop)
        auth.login_guard.reset()
        self.addCleanup(auth.login_guard.reset)
        self.client = TestClient(self.main.app)

    def test_pdf_requires_session(self):
        self.assertEqual(self.client.get("/static-pdfs/rapor.pdf").status_code, 401)

    def test_pdf_is_served_inline_after_login(self):
        self.client.post("/auth/login", json={"password": PASSWORD})
        response = self.client.get("/static-pdfs/rapor.pdf")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, PDF)
        self.assertEqual(response.headers["content-type"], "application/pdf")
        self.assertTrue(response.headers["content-disposition"].startswith("inline"))

    def test_bad_names_and_missing_files(self):
        self.client.post("/auth/login", json={"password": PASSWORD})
        #"..\\" ile klasör dışına çıkma denemesi ve uzantısı PDF olmayan dosya
        for name in ["..%5C.env", "..%5Crapor.pdf", "notlar.txt"]:
            with self.subTest(name=name):
                self.assertEqual(self.client.get(f"/static-pdfs/{name}").status_code, 400)
        self.assertEqual(self.client.get("/static-pdfs/yok.pdf").status_code, 404)


#İleride eklenen bir endpoint'e Depends(require_auth) eklenmeyi unutulursa bu test yakalar
class RouteScanTests(AuthSettingsMixin, unittest.TestCase):

    def setUp(self):
        self.main = import_main()
        self.use_settings()
        self.client = TestClient(self.main.app)

    def api_routes(self):
        for route in self.main.app.routes:
            if isinstance(route, APIRoute):
                for method in route.methods:
                    yield method, route.path

    def test_every_non_public_route_requires_auth(self):
        protected = [(m, p) for m, p in self.api_routes() if (m, p) not in PUBLIC_ROUTES]
        #Tarama boş geçmesin: bilinen korumalı endpoint'ler listede olmalı
        self.assertIn(("POST", "/query/"), protected)
        self.assertIn(("GET", "/static-pdfs/{name}"), protected)
        for method, path in protected:
            with self.subTest(route=f"{method} {path}"):
                response = self.client.request(method, path.replace("{name}", "rapor.pdf"))
                self.assertEqual(response.status_code, 401)

    def test_public_routes_exist(self):
        self.assertTrue(PUBLIC_ROUTES <= set(self.api_routes()))
        self.assertEqual(self.client.get("/").status_code, 200)


if __name__ == "__main__":
    unittest.main()
