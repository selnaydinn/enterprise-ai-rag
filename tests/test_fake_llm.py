import importlib
import unittest
from unittest import mock

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from services import fake_llm
from tests.test_query import QUESTION, chunk

TEXT = "Devamsızlık süresi özürsüz 10 günü aşan öğrenciler başarısız sayılır."


#Sahte LLM modu (LLM_MODE=fake, notes2.md Faz L): yalnızca Gemini adımı değişir, geri kalan akış gerçek
#Bekleme (sleep) her testte sahte; testler gerçekten beklemez
class FakeLlmQueryTests(unittest.TestCase):

    def setUp(self):
        self.query = importlib.import_module("services.query")
        reranker = mock.Mock()
        reranker.predict.side_effect = lambda pairs, **_: [-i for i in range(len(pairs))]
        self.sleep = mock.patch.object(fake_llm, "sleep").start()
        mock.patch.object(self.query, "get_reranker", return_value=reranker).start()
        mock.patch.object(self.query, "search_in_db", return_value=[chunk(0.80, TEXT)]).start()
        #Sahte modda Gemini zinciri hiç kurulmamalı
        self.get_qa_chain = mock.patch.object(self.query, "get_qa_chain").start()
        self.addCleanup(mock.patch.stopall)

    def ask(self, **values):
        base = {"llm_mode": "fake", "fake_llm_delay_seconds": 3, "fake_llm_error_rate": 0,
                "llm_timeout_seconds": 20, "llm_max_attempts": 2, "rerank_score_threshold": -2.0}
        settings = self.query.get_settings().model_copy(update={**base, **values})
        with mock.patch.object(self.query, "get_settings", return_value=settings):
            return self.query.prepare_chunks_with_metadata(QUESTION)

    def test_fake_answer_has_prefix_quote_and_real_sources(self):
        with self.assertLogs("EnterpriseAI", level="INFO"):
            result = self.ask()
        self.assertEqual(result["status"], "success")
        self.assertTrue(result["answer"].startswith(fake_llm.FAKE_PREFIX))
        self.assertIn(TEXT, result["answer"])
        self.assertEqual([(s["source_name"], s["page"]) for s in result["sources"]], [("meb_yonetmelik.pdf", 22)])
        self.assertGreater(result["token_usage"], 0)
        self.get_qa_chain.assert_not_called()
        self.sleep.assert_called_once_with(3)

    #Loglarda Gemini adı geçmez; süre ve token satırı "Sahte LLM" etiketiyle yazılır
    def test_logs_say_fake_llm_not_gemini(self):
        with self.assertLogs("EnterpriseAI", level="INFO") as logs:
            self.ask()
        output = "\n".join(logs.output)
        self.assertIn("Sahte LLM çağrılıyor (Gemini'ye gidilmiyor, 1 parça).", output)
        self.assertRegex(output, r"Süreler: .*Sahte LLM \d+\.\d\d sn")
        self.assertIn("| Sahte LLM token: ", output)
        self.assertNotIn("Gemini API çağrılıyor", output)
        self.assertNotIn("Gemini token", output)

    def test_fake_error_is_logged_as_fake_llm(self):
        with self.assertLogs("EnterpriseAI", level="INFO") as logs, \
             self.assertRaises(self.query.LLMUnavailableError):
            self.ask(fake_llm_error_rate=1, fake_llm_error_kind="connection")
        self.assertTrue(any("Sahte LLM çağrısı başarısız (connection" in line for line in logs.output))

    def test_long_quote_is_shortened(self):
        text = fake_llm.fake_answer_text('<belge kaynak="a.pdf" sayfa="1">\n' + "x" * 500 + "\n</belge>")
        self.assertIn("x" * fake_llm.QUOTE_CHARS + "...", text)
        self.assertNotIn("x" * (fake_llm.QUOTE_CHARS + 1), text)

    #Gerçek en kötü durum kadar bekler (20 sn × 2 deneme), gerçek timeout gibi sınıflandırılır (main.py: 504)
    def test_fake_timeout_waits_like_real_timeout(self):
        with self.assertLogs("EnterpriseAI", level="INFO") as logs, \
             self.assertRaises(self.query.LLMUnavailableError) as raised:
            self.ask(fake_llm_error_rate=1, fake_llm_error_kind="timeout")
        self.assertEqual(raised.exception.kind, "timeout")
        self.sleep.assert_called_once_with(40)
        self.assertTrue(any("WARNING" in line and "timeout" in line for line in logs.output))

    #Bağlantı hatası beklemeden döner (main.py: 503)
    def test_fake_connection_error_returns_immediately(self):
        with self.assertLogs("EnterpriseAI", level="INFO"), \
             self.assertRaises(self.query.LLMUnavailableError) as raised:
            self.ask(fake_llm_error_rate=1, fake_llm_error_kind="connection")
        self.assertEqual(raised.exception.kind, "connection")
        self.sleep.assert_not_called()

    def test_mixed_picks_between_timeout_and_connection(self):
        with mock.patch.object(fake_llm.random, "choice", return_value="connection") as choice, \
             self.assertLogs("EnterpriseAI", level="INFO"), \
             self.assertRaises(self.query.LLMUnavailableError) as raised:
            self.ask(fake_llm_error_rate=1, fake_llm_error_kind="mixed")
        choice.assert_called_once_with(("timeout", "connection"))
        self.assertEqual(raised.exception.kind, "connection")

    #Varsayılan mod: sahte LLM hiç devreye girmez
    def test_gemini_mode_does_not_use_fake(self):
        self.get_qa_chain.return_value = RunnableLambda(lambda _: AIMessage(content="Gerçek cevap"))
        with mock.patch.object(self.query, "invoke_fake_llm") as fake, self.assertLogs("EnterpriseAI", level="INFO"):
            result = self.ask(llm_mode="gemini")
        fake.assert_not_called()
        self.get_qa_chain.assert_called_once()
        self.assertEqual(result["answer"], "Gerçek cevap")


#Açılış (main.py lifespan): sahte modda Gemini istemcisi kurulmaz (key gerekmez) ve WARNING yazılır
class FakeLlmStartupTests(unittest.TestCase):

    def setUp(self):
        self.main = importlib.import_module("main")
        for name in ("check_auth_config", "get_db", "get_reranker", "warm_up", "check_documents"):
            mock.patch.object(self.main, name).start()
        self.get_llm = mock.patch.object(self.main, "get_llm").start()
        self.addCleanup(mock.patch.stopall)

    def start(self, mode):
        with mock.patch.object(self.main, "settings", self.main.settings.model_copy(update={"llm_mode": mode})):
            with TestClient(self.main.app):
                pass

    def test_fake_mode_skips_gemini_client_and_warns(self):
        with self.assertLogs("EnterpriseAI", level="WARNING") as logs:
            self.start("fake")
        self.get_llm.assert_not_called()
        self.assertTrue(any("LLM_MODE=fake" in line for line in logs.output))

    def test_gemini_mode_creates_client(self):
        self.start("gemini")
        self.get_llm.assert_called_once()


if __name__ == "__main__":
    unittest.main()
