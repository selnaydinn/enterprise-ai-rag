import ipaddress
import socket
from langchain_chroma import Chroma
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_huggingface import HuggingFaceEmbeddings

#Testler Gemini'ye, gerçek Chroma DB'ye, embedding modeline ve ağa dokunmamalı (CI'da .env, DB ve model yok)
#Bunlardan biri yanlışlıkla kullanılırsa test açık bir hatayla patlar; tests/__init__.py tüm testlerden önce kuruyor

#Exception değil BaseException: kodda "except Exception" ile yutulup sessizce 500'e dönüşmesin, test mutlaka hata versin
class RealServiceUsedInTest(BaseException):
    pass


_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
_installed = False


def _blocked_init(name, hint):
    def __init__(self, *args, **kwargs):
        raise RealServiceUsedInTest(f"Testlerde gerçek {name} oluşturulamaz. {hint}")
    return __init__


#Kütüphane sınıflarının kendisine konuyor: kod hangi yoldan gelirse gelsin (get_db, doğrudan import) yakalanır
def install():
    global _installed
    if _installed:
        return
    _installed = True

    ChatGoogleGenerativeAI.__init__ = _blocked_init(
        "Gemini istemcisi", "get_llm / get_qa_chain'i mock'layın (bkz. tests/test_query.py).")
    Chroma.__init__ = _blocked_init(
        "Chroma bağlantısı", "get_db'yi mock'layın (bkz. tests/test_upload.py).")
    HuggingFaceEmbeddings.__init__ = _blocked_init(
        "embedding modeli", "get_db / get_embeddings'i mock'layın.")

    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_getaddrinfo = socket.getaddrinfo

    def check_address(address):
        host = address[0] if isinstance(address, tuple) else address
        if host not in _LOCAL_HOSTS:
            raise RealServiceUsedInTest(f"Testlerde ağ bağlantısı açılamaz: {address}")

    #Localhost dışına bağlantı yok: Gemini, HuggingFace veya Chroma telemetrisine yanlışlıkla gidilmesin
    def guarded_connect(self, address):
        check_address(address)
        return real_connect(self, address)

    def guarded_connect_ex(self, address):
        check_address(address)
        return real_connect_ex(self, address)

    #DNS sorgusu da dışarıya gitmesin; IP adresleri ağa çıkmadan çözülüyor, onlar bağlantı aşamasında kontrol ediliyor
    def guarded_getaddrinfo(host, *args, **kwargs):
        if host is not None and host not in _LOCAL_HOSTS and not _is_ip(host):
            raise RealServiceUsedInTest(f"Testlerde DNS sorgusu yapılamaz: {host}")
        return real_getaddrinfo(host, *args, **kwargs)

    socket.socket.connect = guarded_connect
    socket.socket.connect_ex = guarded_connect_ex
    socket.getaddrinfo = guarded_getaddrinfo


def _is_ip(host):
    try:
        ipaddress.ip_address(host.decode() if isinstance(host, bytes) else host)
        return True
    except ValueError:
        return False
