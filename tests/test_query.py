import importlib
import unittest
from unittest import mock

from langchain_core.documents import Document
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

QUESTION = "Özürsüz devamsızlık sınırı kaç gündür?"
USAGE = {"input_tokens": 1450, "output_tokens": 620, "total_tokens": 2070}


#Model, DB ve LLM artık import anında yüklenmiyor (core/config.py); services.query doğrudan import edilebiliyor
def import_query():
    return importlib.import_module("services.query")


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
             mock.patch.object(self.query, "get_qa_chain", return_value=RunnableLambda(lambda _: message)):
            return self.query.prepare_chunks_with_metadata(QUESTION)

    def test_real_token_count_is_used(self):
        result = self.ask(AIMessage(content="Sınır 10 gündür.", usage_metadata=USAGE))
        self.assertEqual(result["token_usage"], 2070)
        self.assertEqual(result["answer"], "Sınır 10 gündür.")
        self.assertEqual(result["status"], "success")

    def test_estimate_is_used_when_usage_metadata_missing(self):
        #Kota sessizce 0 sayılmamalı; tahmin soruyu da içermeli
        with self.assertLogs("EnterpriseAI", level="WARNING"):
            result = self.ask(AIMessage(content="Sınır 10 gündür."))
        self.assertGreater(result["token_usage"], len(QUESTION) // 4)

    def test_list_content_is_returned_as_text(self):
        message = AIMessage(content=[{"type": "text", "text": "Sınır 10 gündür."}], usage_metadata=USAGE)
        self.assertEqual(self.ask(message)["answer"], "Sınır 10 gündür.")

    def test_sources_keep_fields_and_round_score(self):
        result = self.ask(AIMessage(content="Cevap", usage_metadata=USAGE))
        self.assertEqual(result["sources"], [{
            "source_name": "meb_yonetmelik.pdf",
            "page": 22,
            "score": 0.85,
            "raw_text": "Devamsızlık süresi özürsüz 10 günü aşan öğrenciler başarısız sayılır.",
        }])

    def test_llm_is_not_called_when_no_chunk_passes_threshold(self):
        get_qa_chain = mock.Mock()
        with mock.patch.object(self.query, "search_in_db", return_value=[chunk(0.5)]), \
             mock.patch.object(self.query, "get_qa_chain", get_qa_chain):
            result = self.query.prepare_chunks_with_metadata(QUESTION)
        get_qa_chain.assert_not_called()
        self.assertEqual(result["token_usage"], 0)
        self.assertEqual(result["status"], "no_result")
        self.assertEqual(result["sources"], [])

    def test_search_uses_k_from_settings(self):
        db = mock.Mock()
        db.similarity_search_with_relevance_scores.return_value = []
        with mock.patch.object(self.query, "get_db", return_value=db):
            self.query.search_in_db(QUESTION)
        db.similarity_search_with_relevance_scores.assert_called_once_with(
            f"query: {QUESTION}", k=self.query.get_settings().retrieval_k)


if __name__ == "__main__":
    unittest.main()
