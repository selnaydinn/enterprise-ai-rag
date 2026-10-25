import importlib
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

from langchain_core.documents import Document
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

QUESTION = "Özürsüz devamsızlık sınırı kaç gündür?"
USAGE = {"input_tokens": 1450, "output_tokens": 620, "total_tokens": 2070}


_query = None


#services.query import edilirken core.config modeli, DB'yi ve Gemini'yi yüklüyor; sahte config ile bir kez import ediyoruz
#llm'in yerine geçen RunnableLambda, "prompt | llm" zincirinin kurulabilmesi için gerekli
#(Yalnızca bu girdileri geri alıyoruz; tüm sys.modules'ü geri almak numpy gibi modülleri bozar)
def import_query():
    global _query
    if _query is None:
        missing = object()
        names = ("core.config", "services.query")
        saved = {name: sys.modules.get(name, missing) for name in names}
        sys.modules["core.config"] = SimpleNamespace(db=None, llm=RunnableLambda(lambda x: x))
        sys.modules.pop("services.query", None)
        try:
            _query = importlib.import_module("services.query")
        finally:
            for name, module in saved.items():
                if module is missing:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = module
    return _query


def chunk(score):
    doc = Document(page_content="Devamsızlık süresi özürsüz 10 günü aşan öğrenciler başarısız sayılır.",
                   metadata={"source_file": "meb_yonetmelik.pdf", "page_number": 22})
    return (doc, score)


class PrepareChunksTests(unittest.TestCase):

    def setUp(self):
        self.query = import_query()

    #Arama ve LLM sahte; LLM her çağrıda verilen AIMessage'ı döner
    def ask(self, message, chunks=None):
        chunks = [chunk(0.8456)] if chunks is None else chunks
        with mock.patch.object(self.query, "search_in_db", return_value=chunks), \
             mock.patch.object(self.query, "qa_chain", RunnableLambda(lambda _: message)):
            return self.query.prepare_chunks_with_metadata(QUESTION)

    def test_real_token_count_is_used(self):
        result = self.ask(AIMessage(content="Sınır 10 gündür.", usage_metadata=USAGE))
        self.assertEqual(result["token_usage"], 2070)
        self.assertEqual(result["data"], "Sınır 10 gündür.")
        self.assertEqual(result["status"], "Success")

    def test_estimate_is_used_when_usage_metadata_missing(self):
        #Kota sessizce 0 sayılmamalı; tahmin soruyu da içermeli
        with self.assertLogs("EnterpriseAI", level="WARNING"):
            result = self.ask(AIMessage(content="Sınır 10 gündür."))
        self.assertGreater(result["token_usage"], len(QUESTION) // 4)

    def test_list_content_is_returned_as_text(self):
        message = AIMessage(content=[{"type": "text", "text": "Sınır 10 gündür."}], usage_metadata=USAGE)
        self.assertEqual(self.ask(message)["data"], "Sınır 10 gündür.")

    def test_resources_keep_fields_and_round_score(self):
        result = self.ask(AIMessage(content="Cevap", usage_metadata=USAGE))
        self.assertEqual(result["resource"], [{
            "source_name": "meb_yonetmelik.pdf",
            "page": 22,
            "score": 0.85,
            "raw_text": "Devamsızlık süresi özürsüz 10 günü aşan öğrenciler başarısız sayılır.",
        }])

    def test_llm_is_not_called_when_no_chunk_passes_threshold(self):
        qa_chain = mock.Mock()
        with mock.patch.object(self.query, "search_in_db", return_value=[chunk(0.5)]), \
             mock.patch.object(self.query, "qa_chain", qa_chain):
            result = self.query.prepare_chunks_with_metadata(QUESTION)
        qa_chain.invoke.assert_not_called()
        self.assertEqual(result["token_usage"], 0)
        self.assertEqual(result["status"], "Warning")


if __name__ == "__main__":
    unittest.main()
