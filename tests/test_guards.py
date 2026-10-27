import socket
import unittest

from langchain_chroma import Chroma
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_huggingface import HuggingFaceEmbeddings

from core import config
from tests._guards import RealServiceUsedInTest


#Korumaların (tests/_guards.py) gerçekten devrede olduğunu kontrol ediyoruz
class GuardTests(unittest.TestCase):

    def test_real_service_objects_cannot_be_created(self):
        for create in (lambda: ChatGoogleGenerativeAI(model="x", google_api_key="x"),
                       lambda: Chroma(persist_directory="yok"),
                       lambda: HuggingFaceEmbeddings(model_name="x")):
            with self.subTest(create=create), self.assertRaises(RealServiceUsedInTest):
                create()

    def test_guard_is_not_swallowed_by_except_exception(self):
        #Kodda "except Exception" olsa da koruma hatası yukarı çıkmalı
        with self.assertRaises(RealServiceUsedInTest):
            try:
                Chroma(persist_directory="yok")
            except Exception:
                self.fail("Koruma hatası except Exception ile yakalanmamalı")

    def test_factories_cannot_create_real_objects(self):
        #Uygulamanın kendi factory'si üzerinden gelinse de gerçek nesne oluşturulamaz
        config.get_embeddings.cache_clear()
        self.addCleanup(config.get_embeddings.cache_clear)
        with self.assertRaises(RealServiceUsedInTest):
            config.get_embeddings()

    def test_external_network_is_blocked(self):
        #203.0.113.0/24 belgeleme için ayrılmış adres; koruma bağlantıdan önce durduruyor
        with socket.socket() as sock, self.assertRaises(RealServiceUsedInTest):
            sock.connect(("203.0.113.1", 443))

    def test_external_network_is_blocked_for_connect_ex(self):
        with socket.socket() as sock, self.assertRaises(RealServiceUsedInTest):
            sock.connect_ex(("203.0.113.1", 443))

    def test_dns_lookup_is_blocked(self):
        #example.com hiç çözülmeden durduruluyor
        with self.assertRaises(RealServiceUsedInTest):
            socket.getaddrinfo("example.com", 443)

    def test_localhost_is_allowed(self):
        self.assertTrue(socket.getaddrinfo("localhost", 80))
        with socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            server.listen(1)
            with socket.socket() as client:
                client.connect(server.getsockname())


if __name__ == "__main__":
    unittest.main()
