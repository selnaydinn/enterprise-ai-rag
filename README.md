# Enterprise AI

This project is a **Retrieval-Augmented Generation (RAG)** system built with a modular microservices architecture.
It enables scalable document ingestion, high-performance vector semantic search, and secure LLM orchestration wrapped in
full-stack application. It can run locally in a secure environment, reduces tokenization costs and speeds-up the operational 
process.

---

## Architecture

The system is split into decoupled layers to ensure strict separation of concerns, high scalability, and seamless deployment:

* **Backend API (FastAPI):** Asynchronous Python web framework handling document processing pipelines, chunking strategies, and LLM orchestration.
* **Orchestration & LLMs (LangChain):** Manages the RAG pipelines, contextual prompts, and memory structures for multi-turn conversations.
* **Vector Store (ChromaDB):** Embedded, high-efficiency vector database managing text embeddings and sub-second semantic similarity searches.
* **Frontend UI (Next.js & TypeScript):** Modern and responsive interface.
* **Containerization (Docker & Docker Compose):** Container setup guaranteeing identical environments across development and production.

---

## Prerequisites
* Docker & Docker Compose installed.
* Python 3.10+ (if running locally outside Docker). Note: Python dependencies can be found in `requirements.txt`.
* Node.js 18+ (if running frontend locally). Note: Frontend dependencies can be found in `package.json`.

## Running the Application
* Clone the repository and configure your `.env` file with your LLM API keys.
* Spin up the containers (Full Ecosystem): `docker-compose up --build`
* Spin up the frontend locally (Alternative): 
  ```bash
  cd rag-frontend
  npm run dev

* Frontend UI: http://localhost:3000

* Backend API Docs (Swagger): http://localhost:8000/docs

  
  


