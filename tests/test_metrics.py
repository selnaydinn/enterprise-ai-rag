import importlib
import unittest
from unittest import mock

import httpx
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda
from prometheus_client import REGISTRY, generate_latest

from core import metrics
from tests.test_query import QUESTION, USAGE, chunk

NO_RESULT_REASONS = ("e5_threshold", "rerank_threshold", "bilgi_yok", "empty_answer", "empty_db", "unexpected_empty")


#Sayaçlar işlem boyunca birikiyor (testler arasında sıfırlanmıyor); testler önceki ve sonraki değerin farkına bakar
#Hiç oluşmamış etiket kombinasyonu için kütüphane None döner, 0 sayıyoruz
def sample(name, **labels):
    return REGISTRY.get_sample_value(name, labels) or 0.0


class Delta:
    def __init__(self, name, **labels):
        self.name, self.labels = name, labels
        self.before = sample(name, **labels)

    @property
    def value(self):
        return sample(self.name, **self.labels) - self.before


#HTTP metrikleri (core/log_setup.py RequestIdMiddleware → core/metrics.py record_http_request)
class HttpMetricsTests(unittest.TestCase):

    def setUp(self):
        self.main = importlib.import_module("main")
        self.client = TestClient(self.main.app)
        self.get_db = mock.patch.object(self.main, "get_db").start()
        self.addCleanup(mock.patch.stopall)
        self.get_db.return_value.get.return_value = {"ids": []}

    def test_request_is_counted_with_status_and_duration(self):
        count = Delta("http_requests_total", method="GET", route="/", status="200")
        duration = Delta("http_request_duration_seconds_count", method="GET", route="/")
        self.client.get("/")
        self.assertEqual(count.value, 1)
        self.assertEqual(duration.value, 1)

    #Gerçek yol değil adres şablonu: dosya adı etikete girmez
    #Oturum yok (401) ama adres eşleşti; şablon yine görünür
    def test_route_template_is_used_instead_of_path(self):
        count = Delta("http_requests_total", method="GET", route="/static-pdfs/{name}", status="401")
        response = self.client.get("/static-pdfs/gizli_dosya.pdf")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(count.value, 1)
        self.assertEqual(sample("http_requests_total", method="GET", route="unmatched", status="401"), 0)
        self.assertNotIn("gizli_dosya", generate_latest(REGISTRY).decode())

    def test_unknown_path_is_unmatched(self):
        count = Delta("http_requests_total", method="GET", route="unmatched", status="404")
        response = self.client.get("/olmayan-adres-12345")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(count.value, 1)
        self.assertNotIn("olmayan-adres-12345", generate_latest(REGISTRY).decode())

    #CORS ön kontrolünü CORSMiddleware endpoint'e gitmeden cevaplıyor; şablon yok
    def test_cors_preflight_is_unmatched(self):
        headers = {"Origin": self.main.CORS_ORIGINS[0], "Access-Control-Request-Method": "DELETE"}
        before = {s: sample("http_requests_total", method="OPTIONS", route="unmatched", status=s) for s in ("200", "400")}
        response = self.client.options("/documents/x.pdf", headers=headers)
        status = str(response.status_code)
        self.assertEqual(sample("http_requests_total", method="OPTIONS", route="unmatched", status=status) - before.get(status, 0), 1)
        self.assertEqual(sample("http_requests_total", method="OPTIONS", route="/documents/{name}", status=status), 0)

    #Başarılı sağlık kontrolleri sayılmaz (Docker 30 sn'de bir çağırıyor; istek sayısını ve ortalama süreyi bozmasın)
    def test_successful_health_checks_are_not_counted(self):
        counts = [Delta("http_requests_total", method="GET", route=r, status="200") for r in ("/health/live", "/health/ready")]
        durations = [Delta("http_request_duration_seconds_count", method="GET", route=r) for r in ("/health/live", "/health/ready")]
        self.client.get("/health/live")
        self.client.get("/health/ready")
        self.assertEqual([d.value for d in counts + durations], [0, 0, 0, 0])

    def test_failed_health_check_is_counted(self):
        self.get_db.return_value.get.side_effect = RuntimeError("db yok")
        count = Delta("http_requests_total", method="GET", route="/health/ready", status="503")
        with self.assertLogs("EnterpriseAI", level="WARNING"):
            response = self.client.get("/health/ready")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(count.value, 1)


#Soru cevaplama metrikleri (services/query.py); kurulum tests/test_query.py ile aynı
class QueryMetricsTests(unittest.TestCase):

    def setUp(self):
        self.query = importlib.import_module("services.query")
        self.reranker = mock.Mock()
        self.reranker.predict.side_effect = lambda pairs, **_: [-i for i in range(len(pairs))]
        mock.patch.object(self.query, "get_reranker", return_value=self.reranker).start()
        self.addCleanup(mock.patch.stopall)

    def settings_with(self, **values):
        return self.query.get_settings().model_copy(update=values)

    def ask(self, message=None, chunks=None, has_any=True, **settings):
        chunks = [chunk(0.8456)] if chunks is None else chunks
        message = AIMessage(content="Cevap", usage_metadata=USAGE) if message is None else message
        base = {"llm_mode": "gemini", "rerank_score_threshold": -2.0, "reranker_enabled": True}
        with mock.patch.object(self.query, "get_settings", return_value=self.settings_with(**{**base, **settings})), \
             mock.patch.object(self.query, "search_in_db", return_value=chunks), \
             mock.patch.object(self.query, "has_any_chunk", return_value=has_any), \
             mock.patch.object(self.query, "get_qa_chain", return_value=RunnableLambda(lambda _: message)), \
             self.assertLogs("EnterpriseAI", level="INFO"):
            return self.query.prepare_chunks_with_metadata(QUESTION)

    #Her durumda yalnızca kendi sebebi bir artar, diğerleri değişmez
    def assert_only_reason(self, reason, **ask):
        deltas = {r: Delta("rag_no_result_total", reason=r) for r in NO_RESULT_REASONS}
        result = self.ask(**ask)
        self.assertEqual(result["status"], "no_result")
        self.assertEqual({r: d.value for r, d in deltas.items()}, {r: (1 if r == reason else 0) for r in NO_RESULT_REASONS})

    def test_e5_threshold(self):
        self.assert_only_reason("e5_threshold", chunks=[chunk(0.5)])

    def test_rerank_threshold(self):
        self.reranker.predict.side_effect = lambda pairs, **_: [-5.0 for _ in pairs]
        self.assert_only_reason("rerank_threshold")

    def test_bilgi_yok(self):
        self.assert_only_reason("bilgi_yok", message=AIMessage(content="BILGI_YOK", usage_metadata=USAGE))

    def test_empty_answer(self):
        self.assert_only_reason("empty_answer", message=AIMessage(content="", usage_metadata=USAGE))

    def test_empty_db(self):
        self.assert_only_reason("empty_db", chunks=[], has_any=False)

    def test_unexpected_empty(self):
        self.assert_only_reason("unexpected_empty", chunks=[], has_any=True)

    def test_successful_answer_is_not_a_no_result(self):
        deltas = [Delta("rag_no_result_total", reason=r) for r in NO_RESULT_REASONS]
        self.assertEqual(self.ask()["status"], "success")
        self.assertEqual([d.value for d in deltas], [0] * len(NO_RESULT_REASONS))

    #log_timings ile aynı sayılar; çalışmayan aşama ölçülmez
    def test_stage_durations_are_observed(self):
        deltas = {s: Delta("rag_stage_duration_seconds_count", stage=s) for s in ("search", "rerank", "llm")}
        self.ask()
        self.assertEqual({s: d.value for s, d in deltas.items()}, {"search": 1, "rerank": 1, "llm": 1})

    def test_skipped_stages_are_not_observed(self):
        deltas = {s: Delta("rag_stage_duration_seconds_count", stage=s) for s in ("search", "rerank", "llm")}
        self.ask(chunks=[chunk(0.5)])
        self.assertEqual({s: d.value for s, d in deltas.items()}, {"search": 1, "rerank": 0, "llm": 0})

    #Hata olsa da LLM'e kadar geçen süre ölçülür; hata türü sayılır
    def test_llm_error_is_counted_with_kind_and_duration(self):
        error = Delta("llm_errors_total", kind="timeout")
        llm = Delta("rag_stage_duration_seconds_count", stage="llm")
        failing = RunnableLambda(lambda _: (_ for _ in ()).throw(httpx.ReadTimeout("yavaş")))
        with mock.patch.object(self.query, "get_settings", return_value=self.settings_with(llm_mode="gemini")), \
             mock.patch.object(self.query, "search_in_db", return_value=[chunk(0.80)]), \
             mock.patch.object(self.query, "get_qa_chain", return_value=failing), \
             self.assertLogs("EnterpriseAI", level="INFO"), \
             self.assertRaises(self.query.LLMUnavailableError):
            self.query.prepare_chunks_with_metadata(QUESTION)
        self.assertEqual(error.value, 1)
        self.assertEqual(llm.value, 1)

    #output_tokens düşünmeyi de içeriyor: cevap = çıktı - düşünme (logdaki dağılımla aynı)
    def test_gemini_tokens_are_counted_by_kind(self):
        usage = {"input_tokens": 1450, "output_tokens": 620, "total_tokens": 2070, "output_token_details": {"reasoning": 400}}
        deltas = {k: Delta("llm_tokens_total", kind=k) for k in ("input", "thinking", "output")}
        self.ask(message=AIMessage(content="Cevap", usage_metadata=usage))
        self.assertEqual({k: d.value for k, d in deltas.items()}, {"input": 1450, "thinking": 400, "output": 220})

    #Sahte LLM'in token sayıları uydurma; sayılmaz
    def test_fake_llm_tokens_are_not_counted(self):
        deltas = [Delta("llm_tokens_total", kind=k) for k in ("input", "thinking", "output")]
        with mock.patch("services.fake_llm.sleep"):
            result = self.ask(llm_mode="fake", fake_llm_delay_seconds=0, fake_llm_error_rate=0)
        self.assertEqual(result["status"], "success")
        self.assertEqual([d.value for d in deltas], [0, 0, 0])


#Açılış (main.py lifespan): metrik sunucusu yalnızca METRICS_PORT doluysa açılır, kapanışta durdurulur
#Gerçek port açılmıyor (testler birbirini ve çalışan Docker'ı etkilemesin); sunucu fonksiyonları sahte
class MetricsStartupTests(unittest.TestCase):

    def setUp(self):
        self.main = importlib.import_module("main")
        for name in ("check_auth_config", "get_db", "get_llm", "get_reranker", "warm_up", "check_documents"):
            mock.patch.object(self.main, name).start()
        self.start_server = mock.patch.object(self.main, "start_metrics_server", return_value=("sunucu", "thread")).start()
        self.stop_server = mock.patch.object(self.main, "stop_metrics_server").start()
        self.addCleanup(mock.patch.stopall)

    def run_app(self, **values):
        with mock.patch.object(self.main, "settings", self.main.settings.model_copy(update=values)):
            with TestClient(self.main.app):
                pass

    def test_server_starts_and_stops_when_port_is_set(self):
        with self.assertLogs("EnterpriseAI", level="INFO") as logs:
            self.run_app(metrics_port=9100, llm_mode="gemini")
        self.start_server.assert_called_once_with(9100)
        self.stop_server.assert_called_once_with("sunucu", "thread")
        self.assertTrue(any("Metrikler 9100 portunda" in line for line in logs.output))

    def test_server_is_not_started_without_port(self):
        self.run_app(metrics_port=None, llm_mode="gemini")
        self.start_server.assert_not_called()
        self.stop_server.assert_not_called()

    #Aktif mod 1, diğeri 0
    def test_llm_mode_gauge(self):
        with self.assertLogs("EnterpriseAI", level="WARNING"):
            self.run_app(metrics_port=None, llm_mode="fake")
        self.assertEqual((sample("rag_llm_mode", mode="fake"), sample("rag_llm_mode", mode="gemini")), (1, 0))
        self.run_app(metrics_port=None, llm_mode="gemini")
        self.assertEqual((sample("rag_llm_mode", mode="fake"), sample("rag_llm_mode", mode="gemini")), (0, 1))


#core/metrics.py yardımcıları
class MetricsHelperTests(unittest.TestCase):

    def test_created_series_are_disabled(self):
        self.assertNotIn("_created", generate_latest(REGISTRY).decode())

    def test_unknown_token_names_and_zero_counts_are_skipped(self):
        output = Delta("llm_tokens_total", kind="output")
        metrics.record_tokens({"cevap": 0, "bilinmeyen": 5})
        self.assertEqual(output.value, 0)


if __name__ == "__main__":
    unittest.main()
