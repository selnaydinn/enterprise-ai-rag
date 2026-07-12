import os
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_community.vectorstores import Chroma
from langchain_google_genai import ChatGoogleGenerativeAI
from dotenv import load_dotenv 

#API key
load_dotenv()
api_key = os.getenv("GEMINI_API_KEY")
print("Key etkinleştirildi.")


#DB path
chroma_dir = "/app/chroma_db"
os.makedirs(chroma_dir, exist_ok=True)

#Embedding modeli
print("Embedding modeli yükleniyor (multilingual-e5-base)...")
embeddings = HuggingFaceEmbeddings(
    model_name="intfloat/multilingual-e5-base",
    model_kwargs={'device': 'cpu'}
)
print("Embedding modeli yüklendi.")

#DB bağlantısı
print("Veritabanına bağlanılıyor...")
db = Chroma(
    persist_directory=chroma_dir, 
    embedding_function=embeddings
)
print("Veritabanına bağlanıldı.")

#LLM modeli (Gemini)
print("LLM modeli yükleniyor (gemini-2.5-flash)...")
llm = ChatGoogleGenerativeAI(
    model = "gemini-2.5-flash",
    google_api_key = api_key,
    temperature = 0.2
)
print("LLM modeli yüklendi.")