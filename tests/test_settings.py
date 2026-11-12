import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pydantic import ValidationError

from core import config
from core.settings import PROJECT_ROOT, Settings


#Gerçek .env ve ortam değişkenleri testleri etkilemesin: .env okunmuyor, ortam yalnızca verilen değerleri içeriyor
def make_settings(**env):
    with mock.patch.dict(os.environ, env, clear=True):
        return Settings(_env_file=None)


class SettingsTests(unittest.TestCase):

    def test_defaults(self):
        s = make_settings()
        self.assertIsNone(s.gemini_api_key)
        self.assertEqual(s.cors_origins, ["http://localhost:3000", "http://127.0.0.1:3000"])
        self.assertEqual(s.chroma_dir, PROJECT_ROOT / "chroma_db_normalization")
        self.assertEqual((s.token_limit, s.token_window_hours), (10000, 6))
        self.assertEqual((s.retrieval_k, s.score_threshold), (3, 0.72))
        self.assertEqual(s.max_upload_bytes, 20 * 1024 * 1024)

    def test_env_values_are_read(self):
        s = make_settings(TOKEN_LIMIT="500", LLM_MODEL="gemini-x", LLM_TEMPERATURE="0.5")
        self.assertEqual((s.token_limit, s.llm_model, s.llm_temperature), (500, "gemini-x", 0.5))

    def test_cors_origins_are_comma_separated(self):
        s = make_settings(CORS_ORIGINS=" http://a:3000 , http://b:3000 ,")
        self.assertEqual(s.cors_origins, ["http://a:3000", "http://b:3000"])

    def test_relative_paths_resolve_from_project_root_absolute_kept(self):
        absolute = str(Path(tempfile.gettempdir()) / "baska_db")
        s = make_settings(CHROMA_DIR="db_x", USAGE_DB_PATH=absolute)
        self.assertEqual(s.chroma_dir, PROJECT_ROOT / "db_x")
        self.assertEqual(s.usage_db_path, Path(absolute))

    def test_empty_values_fall_back_to_defaults(self):
        s = make_settings(CHROMA_DIR="", TOKEN_LIMIT="", GEMINI_API_KEY="   ")
        self.assertEqual(s.chroma_dir, PROJECT_ROOT / "chroma_db_normalization")
        self.assertEqual(s.token_limit, 10000)
        self.assertIsNone(s.gemini_api_key)

    def test_api_key_is_stripped_and_hidden(self):
        s = make_settings(GEMINI_API_KEY="  gizli  ")
        self.assertEqual(s.gemini_api_key.get_secret_value(), "gizli")
        self.assertNotIn("gizli", repr(s))

    def test_auth_defaults(self):
        s = make_settings()
        self.assertTrue(s.auth_enabled)
        self.assertIsNone(s.app_password)
        self.assertIsNone(s.session_secret)
        self.assertEqual(s.session_hours, 12)
        self.assertFalse(s.cookie_secure)

    def test_auth_secrets_are_stripped_hidden_and_blank_is_none(self):
        secret = "s" * 40
        s = make_settings(APP_PASSWORD="  parola  ", SESSION_SECRET=f" {secret} ")
        self.assertEqual(s.app_password.get_secret_value(), "parola")
        self.assertEqual(s.session_secret.get_secret_value(), secret)
        self.assertNotIn("parola", repr(s))
        self.assertNotIn(secret, repr(s))
        blank = make_settings(APP_PASSWORD="   ", SESSION_SECRET="   ")
        self.assertIsNone(blank.app_password)
        self.assertIsNone(blank.session_secret)

    def test_short_session_secret_is_rejected(self):
        with self.assertRaises(ValidationError):
            make_settings(SESSION_SECRET="k" * 31)
        self.assertEqual(len(make_settings(SESSION_SECRET="k" * 32).session_secret.get_secret_value()), 32)

    def test_session_hours_must_be_positive(self):
        for hours in ["0", "-1"]:
            with self.subTest(hours=hours), self.assertRaises(ValidationError):
                make_settings(SESSION_HOURS=hours)

    def test_window_hours_must_divide_day(self):
        for hours in ["5", "0", "-6"]:
            with self.subTest(hours=hours), self.assertRaises(ValidationError):
                make_settings(TOKEN_WINDOW_HOURS=hours)
        self.assertEqual(make_settings(TOKEN_WINDOW_HOURS="1").token_window_hours, 1)

    def test_log_level_is_read_case_insensitive(self):
        self.assertEqual(make_settings().log_level, "INFO")
        self.assertEqual(make_settings(LOG_LEVEL=" debug ").log_level, "DEBUG")

    def test_invalid_log_level_is_rejected(self):
        with self.assertRaises(ValidationError):
            make_settings(LOG_LEVEL="VERBOSE")

    def test_log_format_defaults_to_text_and_is_case_insensitive(self):
        self.assertEqual(make_settings().log_format, "text")
        self.assertEqual(make_settings(LOG_FORMAT=" JSON ").log_format, "json")

    def test_invalid_log_format_is_rejected(self):
        with self.assertRaises(ValidationError):
            make_settings(LOG_FORMAT="xml")

    def test_thinking_budget_defaults_to_model_default(self):
        self.assertIsNone(make_settings().llm_thinking_budget)
        for value, expected in (("-1", -1), ("0", 0), ("256", 256)):
            with self.subTest(value=value):
                self.assertEqual(make_settings(LLM_THINKING_BUDGET=value).llm_thinking_budget, expected)

    def test_thinking_budget_below_dynamic_is_rejected(self):
        with self.assertRaises(ValidationError):
            make_settings(LLM_THINKING_BUDGET="-5")

    def test_llm_timeout_and_attempts(self):
        s = make_settings()
        self.assertEqual((s.llm_timeout_seconds, s.llm_max_attempts), (20, 2))
        s = make_settings(LLM_TIMEOUT_SECONDS="7.5", LLM_MAX_ATTEMPTS="1")
        self.assertEqual((s.llm_timeout_seconds, s.llm_max_attempts), (7.5, 1))
        for env in ({"LLM_TIMEOUT_SECONDS": "0"}, {"LLM_TIMEOUT_SECONDS": "-3"}, {"LLM_MAX_ATTEMPTS": "0"}):
            with self.subTest(env=env), self.assertRaises(ValidationError):
                make_settings(**env)

    def test_invalid_number_is_rejected(self):
        with self.assertRaises(ValidationError):
            make_settings(TOKEN_LIMIT="on bin")


#Başlangıç kontrolleri: key veya DB eksikse factory'ler hata vermeli (main.py lifespan bunları çağırıyor)
class FactoryTests(unittest.TestCase):

    def setUp(self):
        for factory in (config.get_llm, config.get_db, config.get_embeddings):
            factory.cache_clear()
            self.addCleanup(factory.cache_clear)

    def test_get_llm_without_key_fails(self):
        with mock.patch.object(config, "get_settings", return_value=make_settings()), \
             self.assertRaisesRegex(RuntimeError, "GEMINI_API_KEY"):
            config.get_llm()

    def test_get_db_without_chroma_files_fails_and_creates_nothing(self):
        root = Path(tempfile.mkdtemp(prefix="settings_test_"))
        self.addCleanup(shutil.rmtree, root, True)
        missing = root / "yok"
        with mock.patch.object(config, "get_settings", return_value=make_settings(CHROMA_DIR=str(missing))), \
             mock.patch.object(config, "get_embeddings") as embeddings, \
             self.assertRaisesRegex(RuntimeError, "Chroma DB bulunamadı"):
            config.get_db()
        #Boş bir DB açılmamalı ve model boşuna yüklenmemeli
        self.assertFalse(missing.exists())
        embeddings.assert_not_called()

    #Gemini istemcisi sahte; düşünme bütçesi yalnızca ayarlandıysa gönderilmeli (yoksa model varsayılanı kalır)
    def llm_kwargs(self, **env):
        with mock.patch.object(config, "get_settings", return_value=make_settings(GEMINI_API_KEY="x", **env)), \
             mock.patch.object(config, "ChatGoogleGenerativeAI") as client:
            config.get_llm()
        return client.call_args.kwargs

    def test_thinking_budget_is_not_sent_by_default(self):
        self.assertNotIn("thinking_budget", self.llm_kwargs())

    def test_timeout_and_attempts_are_sent(self):
        kwargs = self.llm_kwargs()
        self.assertEqual((kwargs["timeout"], kwargs["max_retries"]), (20, 2))
        config.get_llm.cache_clear()
        kwargs = self.llm_kwargs(LLM_TIMEOUT_SECONDS="5", LLM_MAX_ATTEMPTS="1")
        self.assertEqual((kwargs["timeout"], kwargs["max_retries"]), (5, 1))

    def test_thinking_budget_is_sent_when_set(self):
        for value, expected in (("0", 0), ("256", 256), ("-1", -1)):
            with self.subTest(value=value):
                config.get_llm.cache_clear()
                self.assertEqual(self.llm_kwargs(LLM_THINKING_BUDGET=value)["thinking_budget"], expected)


if __name__ == "__main__":
    unittest.main()
