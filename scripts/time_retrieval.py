#Arama ve reranker süresini ölçer (notes2.md Faz 2, §17: Docker yavaşlığının kaynağı)
#Gemini çağrılmaz, token harcanmaz. DB yalnızca okunur
#
#Uygulamanın kendi fonksiyonları kullanılır (services/query.py: search_in_db, rerank_chunks): soru sorulduğunda çalışan
#yolun aynısı; sonuçlar logdaki "Süreler: arama ... · rerank ..." satırıyla karşılaştırılabilir
#eval/ kodu kullanılmıyor: eval kaliteyi ölçer ve exact search kullanır, uygulama HNSW kullanır (notes.md #41)
#Sorular eval/golden_set.jsonl'den seçilir; her çalıştırmada aynı sorular (ölçümler birbiriyle karşılaştırılabilsin)
#
#  python scripts/time_retrieval.py                               (Windows, Docker'sız)
#  docker exec meb_rag_backend python scripts/time_retrieval.py   (container içinde)
#  python scripts/time_retrieval.py --questions 10 --rounds 5
#  python scripts/time_retrieval.py --questions 3 --rounds 1 --pause 180   (boşta bekleme etkisi, ~10 dk sürer)
#
#--pause: her sorudan önce verilen süre beklenir, soru bir kez "bekledikten sonra" bir kez de hemen arkasından
#sorulur; iki grup ayrı raporlanır (uygulamada birkaç dakika boşta kalınca ilk soru yavaşlıyordu, notes2.md Faz 2)
#
#Başka bir DB'yi (ör. bir kopyayı) ölçmek için CHROMA_DIR ortam değişkeni verilir; .env'deki değerin önüne geçer

import argparse
import json
import logging
import os
import statistics
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

#Modeller yalnızca yerel önbellekten okunur; ölçüm sırasında internetten bir şey inmesin (Docker'da compose zaten 1 yapıyor)
os.environ.setdefault("HF_HUB_OFFLINE", "1")

GOLDEN_PATH = PROJECT_ROOT / "eval" / "golden_set.jsonl"


#Golden set'ten eşit aralıklarla n soru: farklı PDF'lerden ve farklı uzunluklardan sorular gelsin
def pick_questions(n):
    records = [json.loads(line) for line in GOLDEN_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    if n >= len(records):
        return [r["question"] for r in records]
    step = len(records) / n
    return [records[int(i * step)]["question"] for i in range(n)]


def summarize(values):
    if not values:
        return "ölçülmedi"
    return f"ortanca {statistics.median(values):.2f} sn · en hızlı {min(values):.2f} · en yavaş {max(values):.2f} (n={len(values)})"


def main():
    parser = argparse.ArgumentParser(description="Arama ve reranker süresini ölçer (Gemini çağrılmaz).")
    parser.add_argument("--questions", type=int, default=5, help="Golden set'ten kaç soru (varsayılan 5)")
    parser.add_argument("--rounds", type=int, default=3, help="Her soru kaç kez (varsayılan 3)")
    parser.add_argument("--pause", type=float, default=0,
                        help="Her sorudan önce kaç sn beklensin; verilirse soru hemen arkasından bir kez daha sorulur (varsayılan 0)")
    args = parser.parse_args()

    #search_in_db her soruyu INFO'da logluyor; ölçüm çıktısı kalabalıklaşmasın
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    #Import burada: HF_HUB_OFFLINE ayarlandıktan sonra
    import torch
    from core.config import get_db, get_reranker
    from core.settings import get_settings
    from services.query import rerank_chunks, search_in_db, warm_up

    settings = get_settings()
    questions = pick_questions(args.questions)

    print("Ortam")
    print(f"  Docker içinde:     {'evet' if Path('/.dockerenv').exists() else 'hayır'}")
    print(f"  CPU çekirdeği:     {os.cpu_count()} (torch iş parçacığı: {torch.get_num_threads()}, OMP_NUM_THREADS: {os.environ.get('OMP_NUM_THREADS', 'tanımsız')})")
    print(f"  DB:                {settings.chroma_dir}")
    print(f"  Reranker:          {'açık' if settings.reranker_enabled else 'kapalı'}")
    print(f"  Ölçüm:             {len(questions)} soru × {args.rounds} tur")
    if args.pause:
        total_minutes = len(questions) * args.rounds * args.pause / 60
        print(f"  Bekleme:           her sorudan önce {args.pause:.0f} sn (toplam ~{total_minutes:.0f} dk)")
    print()

    #Model yükleme ve ısınma ayrı ölçülür; uygulama bunları açılışta yapıyor, kullanıcı sorusunun süresine girmez
    step = time.perf_counter()
    get_db()
    if settings.reranker_enabled:
        get_reranker()
    load_seconds = time.perf_counter() - step

    step = time.perf_counter()
    warm_up()
    warm_up_seconds = time.perf_counter() - step

    #Her grup: arama, reranker, toplam süre listeleri
    def new_group():
        return {"search": [], "rerank": [], "total": []}

    #services/query.py answer_question'daki sırayla: arama → e5 barajı → reranker (Gemini'den önce durur)
    def measure(question, group):
        start = time.perf_counter()
        matched = search_in_db(question)
        group["search"].append(time.perf_counter() - start)

        passed = [(doc, score) for doc, score in matched if score >= settings.score_threshold]
        #Uygulamadaki gibi: reranker yalnızca açıkken ve barajı geçen parça varken çalışır
        if settings.reranker_enabled and passed:
            step = time.perf_counter()
            rerank_chunks(question, passed)
            group["rerank"].append(time.perf_counter() - step)

        group["total"].append(time.perf_counter() - start)

    after_pause, right_after = new_group(), new_group()
    for _ in range(args.rounds):
        for question in questions:
            if args.pause:
                time.sleep(args.pause)
                measure(question, after_pause)
            measure(question, right_after)

    def print_group(title, group):
        print(f"  {title}")
        print(f"    Arama:             {summarize(group['search'])}")
        print(f"    Reranker:          {summarize(group['rerank'])}")
        print(f"    Arama + reranker:  {summarize(group['total'])}")

    print("Sonuç")
    print(f"  Model yükleme:     {load_seconds:.2f} sn")
    print(f"  Isınma:            {warm_up_seconds:.2f} sn")
    if args.pause:
        print_group(f"{args.pause:.0f} sn bekledikten sonra:", after_pause)
        print_group("Hemen arkasından (aynı soru):", right_after)
    else:
        print_group("Sorular arka arkaya:", right_after)


if __name__ == "__main__":
    main()
