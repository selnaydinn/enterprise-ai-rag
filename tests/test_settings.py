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


if __name__ == "__main__":
    unittest.main()
