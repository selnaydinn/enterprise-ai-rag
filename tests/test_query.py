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


def chunk(score, text="Devamsızlık süresi özürsüz 10 günü aşan öğrenciler başarısız sayılır."):
    doc = Document(page_content=text, metadata={"source_file": "meb_yonetmelik.pdf", "page_number": 22})
    return (doc, score)


class PrepareChunksTests(unittest.TestCase):

    def setUp(self):
        self.query = import_query()
        #Reranker sahte; varsayılan olarak azalan skor verip e5 sırasını korur
        self.reranker = mock.Mock()
        self.reranker.predict.side_effect = lambda pairs: [-i for i in range(len(pairs))]
        patch = mock.patch.object(self.query, "get_reranker", return_value=self.reranker)
        patch.start()
        self.addCleanup(patch.stop)

    def texts(self, result):
        return [s["raw_text"] for s in result["sources"]]

    def settings_with(self, **values):
        return self.query.get_settings().model_copy(update=values)

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

    def test_reranker_reorders_and_keeps_retrieval_k(self):
        chunks = [chunk(0.80, "A"), chunk(0.79, "B"), chunk(0.78, "C"), chunk(0.77, "D")]
        self.reranker.predict.side_effect = lambda pairs: [0.1, 5.0, -1.0, 3.0]
        with mock.patch.object(self.query, "get_settings", return_value=self.settings_with(retrieval_k=3)):
            result = self.ask(AIMessage(content="Cevap", usage_metadata=USAGE), chunks)
        self.assertEqual(self.texts(result), ["B", "D", "A"])
        #Soru reranker'a e5'in "query:" ön eki olmadan gider
        self.reranker.predict.assert_called_once_with([(QUESTION, t) for t in "ABCD"])

    def test_threshold_is_applied_before_reranker(self):
        #Barajın altındaki parça reranker skoru ne olursa olsun Gemini'ye gitmemeli
        chunks = [chunk(0.80, "A"), chunk(0.50, "B"), chunk(0.75, "C")]
        self.reranker.predict.side_effect = lambda pairs: [1.0, 2.0]
        result = self.ask(AIMessage(content="Cevap", usage_metadata=USAGE), chunks)
        self.reranker.predict.assert_called_once_with([(QUESTION, "A"), (QUESTION, "C")])
        self.assertEqual(self.texts(result), ["C", "A"])

    def test_equal_reranker_scores_keep_vector_order(self):
        chunks = [chunk(0.80, "A"), chunk(0.79, "B"), chunk(0.78, "C")]
        self.reranker.predict.side_effect = lambda pairs: [1.0, 1.0, 1.0]
        result = self.ask(AIMessage(content="Cevap", usage_metadata=USAGE), chunks)
        self.assertEqual(self.texts(result), ["A", "B", "C"])

    def test_llm_is_not_called_when_best_rerank_score_is_below_threshold(self):
        #Parçalar e5 barajını geçse de en alakalısı reranker barajının altındaysa soru cevaplanamıyor sayılır
        chunks = [chunk(0.80, "A"), chunk(0.79, "B")]
        self.reranker.predict.side_effect = lambda pairs: [-3.0, -2.5]
        get_qa_chain = mock.Mock()
        with mock.patch.object(self.query, "get_settings", return_value=self.settings_with(rerank_score_threshold=-2.0)), \
             mock.patch.object(self.query, "search_in_db", return_value=chunks), \
             mock.patch.object(self.query, "get_qa_chain", get_qa_chain):
            result = self.query.prepare_chunks_with_metadata(QUESTION)
        get_qa_chain.assert_not_called()
        self.assertEqual(result["status"], "no_result")
        self.assertEqual(result["token_usage"], 0)

    def test_rerank_threshold_is_inclusive_and_checks_the_best_score(self):
        #Tek bir parçanın barajı geçmesi yeterli; en iyi skor tam baraj değerindeyse soru geçer
        chunks = [chunk(0.80, "A"), chunk(0.79, "B")]
        self.reranker.predict.side_effect = lambda pairs: [-5.0, -2.0]
        with mock.patch.object(self.query, "get_settings", return_value=self.settings_with(rerank_score_threshold=-2.0)):
            result = self.ask(AIMessage(content="Cevap", usage_metadata=USAGE), chunks)
        self.assertEqual(result["status"], "success")
        self.assertEqual(self.texts(result), ["B", "A"])

    def test_single_chunk_is_also_checked_by_reranker(self):
        #Barajı geçen tek parça da reranker'dan geçer; sıralama gerekmese de baraj uygulanmalı
        self.reranker.predict.side_effect = lambda pairs: [-5.0]
        with mock.patch.object(self.query, "get_settings", return_value=self.settings_with(rerank_score_threshold=-2.0)), \
             mock.patch.object(self.query, "get_qa_chain") as get_qa_chain:
            with mock.patch.object(self.query, "search_in_db", return_value=[chunk(0.80)]):
                result = self.query.prepare_chunks_with_metadata(QUESTION)
        get_qa_chain.assert_not_called()
        self.assertEqual(result["status"], "no_result")

    def test_reranker_is_not_used_when_disabled(self):
        chunks = [chunk(0.80, "A"), chunk(0.79, "B"), chunk(0.78, "C"), chunk(0.77, "D")]
        settings = self.settings_with(reranker_enabled=False, retrieval_k=3)
        with mock.patch.object(self.query, "get_settings", return_value=settings):
            result = self.ask(AIMessage(content="Cevap", usage_metadata=USAGE), chunks)
        self.reranker.predict.assert_not_called()
        self.assertEqual(self.texts(result), ["A", "B", "C"])

    def test_search_uses_candidate_k_when_reranker_enabled(self):
        db = mock.Mock()
        db.similarity_search_with_relevance_scores.return_value = []
        settings = self.settings_with(reranker_enabled=True)
        with mock.patch.object(self.query, "get_db", return_value=db), \
             mock.patch.object(self.query, "get_settings", return_value=settings):
            self.query.search_in_db(QUESTION)
        db.similarity_search_with_relevance_scores.assert_called_once_with(
            f"query: {QUESTION}", k=settings.rerank_candidate_k)

    def test_search_uses_retrieval_k_when_reranker_disabled(self):
        db = mock.Mock()
        db.similarity_search_with_relevance_scores.return_value = []
        settings = self.settings_with(reranker_enabled=False)
        with mock.patch.object(self.query, "get_db", return_value=db), \
             mock.patch.object(self.query, "get_settings", return_value=settings):
            self.query.search_in_db(QUESTION)
        db.similarity_search_with_relevance_scores.assert_called_once_with(
            f"query: {QUESTION}", k=settings.retrieval_k)


if __name__ == "__main__":
    unittest.main()
