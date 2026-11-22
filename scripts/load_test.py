#Yük testi (notes2.md Faz L, §16): çalışan uygulamaya eşzamanlı soru gönderir, süreleri ve durum kodlarını özetler
#
#Kapalı döngü: --concurrency N, aynı anda N kişi demek; her biri sorusunu gönderir, cevabı bekler, sonra sıradakini
#gönderir (saniyede N istek değil). Birden fazla değer verilirse düzeyler sırayla çalışır, her düzeyde aynı sorular
#
#YALNIZCA sahte LLM modunda çalışır (LLM_MODE=fake, docs/configuration.md): /health/ready "fake" demiyorsa başlamaz,
#gerçek Gemini kotası harcanmaz. Production hâliyle (--reload yok) çalıştırılmalı:
#  docker compose -f docker-compose.yml up -d --build
#
#  python scripts/load_test.py --concurrency 1 2 --limit 20                 (ilk deneme, ~5 dk)
#  python scripts/load_test.py --concurrency 1 2 4 8                        (tam koşu, ~1 saat)
#  python scripts/load_test.py --concurrency 4 --limit 40 --container meb_rag_backend   (CPU ve throttling ile)
#  python scripts/load_test.py --pause 120 --limit 5                        (boşta bekleme etkisi, ~10 dk)
#
#--container meb_rag_backend: her düzeyde container'ın CPU kullanımı ve throttling'i (Docker kotası dolunca durdurulma)
#ölçülür; container'daki OMP_NUM_THREADS de sonuca yazılır
#Sonuçlar (özet + istek bazında) scripts/stress-results/ altına JSON olarak yazılır (.gitignore'da); --out ile başka yol
#Parola .env'deki APP_PASSWORD'dan okunur. RAM'i ayrı bir terminalde izleyin: docker stats meb_rag_backend
#Her isteğin X-Request-ID'si "load-<saat>-c<N>-<sıra>": logdaki "Süreler:" satırı bu ID ile bulunur

import argparse
import json
import math
import subprocess
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

GOLDEN_PATH = PROJECT_ROOT / "eval" / "golden_set.jsonl"
NEGATIVE_PATH = PROJECT_ROOT / "eval" / "negative_set.jsonl"
REQUEST_ID_HEADER = "X-Request-ID"
#Sonuçlar varsayılan olarak buraya yazılır (.gitignore'da; ölçümler yerel kalır)
RESULTS_DIR = PROJECT_ROOT / "scripts" / "stress-results"
#Container'ın CPU sayaçları (cgroup v2): kullanılan CPU süresi ve kota dolunca durdurulma (throttling)
#nr_periods: geçen 100 ms'lik dönem · nr_throttled: kotanın bitip container'ın durdurulduğu dönem · throttled_usec: durdurulmuş süre
CPU_STAT_PATH = "/sys/fs/cgroup/cpu.stat"
CPU_STAT_KEYS = ("usage_usec", "nr_periods", "nr_throttled", "throttled_usec")


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


#Listeden eşit aralıklarla n kayıt (farklı PDF'lerden ve konulardan sorular gelsin)
def evenly(records, n):
    if n >= len(records):
        return records
    if n <= 0:
        return []
    step = len(records) / n
    return [records[int(i * step)] for i in range(n)]


#limit verilirse her setten oranı kadar soru seçilir (20 soruda 12 golden + 8 negatif), sonra iki set dosyadaki
#sıralarına göre eşit aralıklarla karıştırılır. Rastgele değil: her koşuda aynı sorular, ölçümler karşılaştırılabilsin
def pick_questions(question_set="all", limit=None, golden_path=GOLDEN_PATH, negative_path=NEGATIVE_PATH):
    groups = []
    if question_set in ("all", "golden"):
        groups.append(("golden", read_jsonl(golden_path)))
    if question_set in ("all", "negative"):
        groups.append(("negative", read_jsonl(negative_path)))
    total = sum(len(records) for _, records in groups)
    if limit is not None and limit < total:
        #Sonuncu grup kalanı alır; toplam her zaman tam limit
        remaining = limit
        for index, (kind, records) in enumerate(groups):
            count = remaining if index == len(groups) - 1 else round(limit * len(records) / total)
            groups[index] = (kind, evenly(records, count))
            remaining -= len(groups[index][1])
    placed = []
    for kind, records in groups:
        for i, record in enumerate(records):
            placed.append(((i + 0.5) / len(records), kind, record))
    placed.sort(key=lambda item: item[0])
    return [{"id": r["id"], "kind": kind, "question": r["question"]} for _, kind, r in placed]


#En yakın sıra yöntemi: p95, sıralı listede değerlerin %95'inin altında kaldığı ilk değer
def percentile(values, p):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(p / 100 * len(ordered)) - 1)]


def latency(values):
    if not values:
        return None
    return {"n": len(values), "p50": percentile(values, 50), "p95": percentile(values, 95), "max": max(values)}


#Container içinde komut çalıştırır; Docker yoksa, container bulunamazsa veya komut başarısızsa None
def docker_exec(container, *command):
    try:
        done = subprocess.run(["docker", "exec", container, *command], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout if done.returncode == 0 else None


#"anahtar değer" satırları → sözlük (sayı olmayan satırlar atlanır)
def parse_cpu_stat(text):
    values = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].isdigit():
            values[parts[0]] = int(parts[1])
    return values


#Okunamazsa None: CPU bilgisi olmadan devam edilir, yük testi durmaz
def read_cpu_stat(container):
    if not container:
        return None
    text = docker_exec(container, "cat", CPU_STAT_PATH)
    values = parse_cpu_stat(text) if text else {}
    return values if "usage_usec" in values else None


#Bir düzeyin başı ve sonu arasındaki fark. avg_cores ~2 ise kota (cpus: 2) dolu kullanılmış
#Kota tanımlı değilse nr_periods artmaz; oran None
def cpu_delta(before, after, wall_seconds):
    if not before or not after:
        return None
    diff = {key: after.get(key, 0) - before.get(key, 0) for key in CPU_STAT_KEYS}
    return {
        "avg_cores": round(diff["usage_usec"] / 1e6 / wall_seconds, 2) if wall_seconds > 0 else None,
        "periods": diff["nr_periods"],
        "throttled_periods": diff["nr_throttled"],
        "throttled_ratio": round(diff["nr_throttled"] / diff["nr_periods"], 3) if diff["nr_periods"] else None,
        "throttled_seconds": round(diff["throttled_usec"] / 1e6, 1),
    }


#Uygulama hazır mı ve sahte modda mı; değilse çıkar (gerçek Gemini'ye yük gitmesin)
def check_ready(client):
    try:
        response = client.get("/health/ready")
    except httpx.TransportError as exc:
        sys.exit(f"Uygulamaya ulaşılamadı ({type(exc).__name__}). Container açık mı?")
    if response.status_code != 200:
        sys.exit(f"Uygulama hazır değil (/health/ready {response.status_code}).")
    mode = response.json().get("llm_mode")
    if mode != "fake":
        sys.exit(f"Sahte LLM modu kapalı (llm_mode: {mode}). Gerçek Gemini kotası harcanmasın diye test başlamadı. "
                 f".env'de LLM_MODE=fake yapıp container'ı yeniden derleyin/başlatın.")


#Tek giriş; ilk hatada durur (5 hatalı deneme girişi 15 dk kilitler, tekrar denenmez). Parola yazdırılmaz
def login(client, password, auth_enabled=True):
    if not auth_enabled:
        return
    if password is None:
        sys.exit("APP_PASSWORD .env'de tanımlı değil.")
    response = client.post("/auth/login", json={"password": password})
    if response.status_code != 204:
        sys.exit(f"Giriş başarısız ({response.status_code}). Parolayı kontrol edin; tekrar denenmedi.")
    #Cookie gerçekten saklandı mı (httpx cookie'yi sonraki isteklere ekliyor mu)
    if client.get("/auth/me").status_code != 200:
        sys.exit("Giriş yapıldı ama oturum cookie'si sonraki isteklerde gönderilmedi.")


#Tek soru; HTTP kodu yerine istemci tarafı hatalar "zaman aşımı" / "bağlantı" olarak yazılır
def send_question(client, item, request_id):
    start = time.perf_counter()
    result = {"id": item["id"], "kind": item["kind"], "request_id": request_id, "http": None, "status": None}
    try:
        response = client.post("/query/", json={"question": item["question"]}, headers={REQUEST_ID_HEADER: request_id})
        result["http"] = response.status_code
        if response.status_code == 200:
            result["status"] = response.json().get("status")
    except httpx.TimeoutException:
        result["http"] = "zaman aşımı"
    except httpx.TransportError:
        result["http"] = "bağlantı"
    result["seconds"] = time.perf_counter() - start
    return result


#Kapalı döngü: havuzda concurrency kadar işçi; biri bitince sıradaki soruyu alır
#pause > 0 ise sorular tek tek ve her birinden önce beklenerek gönderilir (concurrency 1 olmalı)
def run_level(client, questions, concurrency, run_id, pause=0, progress=print):
    lock = threading.Lock()
    done = [0]

    def one(index_item):
        index, item = index_item
        if pause:
            time.sleep(pause)
        result = send_question(client, item, f"{run_id}-c{concurrency}-{index:03d}")
        with lock:
            done[0] += 1
            progress(f"  [{done[0]}/{len(questions)}] {item['id']} {result['http']} "
                     f"{result['status'] or ''} {result['seconds']:.1f} sn")
        return result

    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        results = list(pool.map(one, enumerate(questions, 1)))
    return results, time.perf_counter() - start


#pause verildiyse bekleme süresi duvar saatinden düşülür; saniyedeki istek gerçek işlem süresine göre
#cpu: cpu_delta sonucu (--container verilmediyse veya okunamadıysa None)
def summarize(results, wall_seconds, pause=0, cpu=None):
    ok = [r for r in results if r["http"] == 200]
    busy_seconds = wall_seconds - pause * len(results)
    return {
        "cpu": cpu,
        "requests": len(results),
        "wall_seconds": round(wall_seconds, 2),
        "requests_per_second": round(len(results) / busy_seconds, 3) if busy_seconds > 0 else None,
        "http": dict(Counter(str(r["http"]) for r in results)),
        "answer": dict(Counter(r["status"] for r in ok)),
        "latency": {
            "all": latency([r["seconds"] for r in results]),
            "success": latency([r["seconds"] for r in ok if r["status"] == "success"]),
            "no_result": latency([r["seconds"] for r in ok if r["status"] == "no_result"]),
            "error": latency([r["seconds"] for r in results if r["http"] != 200]),
        },
    }


def format_summary(concurrency, summary):
    lines = [f"Eşzamanlılık {concurrency}: {summary['requests']} istek, {summary['wall_seconds']:.1f} sn, "
             f"saniyede {summary['requests_per_second']} istek",
             f"  HTTP: {summary['http']} · cevap: {summary['answer']}"]
    for name, values in summary["latency"].items():
        if values:
            lines.append(f"  {name:<9} n={values['n']:<4} p50 {values['p50']:.2f} sn · p95 {values['p95']:.2f} sn · "
                         f"en uzun {values['max']:.2f} sn")
    cpu = summary.get("cpu")
    if cpu:
        ratio = "yok (kota tanımsız)" if cpu["throttled_ratio"] is None else f"%{cpu['throttled_ratio'] * 100:.0f}"
        lines.append(f"  CPU: ort. {cpu['avg_cores']} çekirdek · frenlenen dönem {ratio} "
                     f"({cpu['throttled_periods']}/{cpu['periods']}) · durdurulmuş {cpu['throttled_seconds']} sn")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Yük testi: çalışan uygulamaya eşzamanlı soru gönderir (yalnızca LLM_MODE=fake).")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="Uygulamanın adresi (varsayılan http://127.0.0.1:8000)")
    parser.add_argument("--concurrency", type=int, nargs="+", default=[1], help="Aynı anda kaç kişi; birden fazla değer sırayla çalışır (ör. 1 2 4 8)")
    parser.add_argument("--set", dest="question_set", choices=("all", "golden", "negative"), default="all", help="Soru seti (varsayılan all: 80 golden + 55 negatif)")
    parser.add_argument("--limit", type=int, help="Her düzeyde kaç soru (varsayılan hepsi); golden ve negatif oranında karışık seçilir")
    parser.add_argument("--pause", type=float, default=0, help="Her sorudan önce kaç sn beklensin (boşta bekleme etkisi; yalnızca --concurrency 1)")
    parser.add_argument("--timeout", type=float, default=180, help="İstek başına zaman aşımı, sn (varsayılan 180)")
    parser.add_argument("--container", help="Docker container adı (ör. meb_rag_backend); verilirse her düzeyde CPU kullanımı ve throttling ölçülür")
    parser.add_argument("--out", type=Path, help="Sonuç dosyası (özet + istek bazında JSON); varsayılan scripts/stress-results/load-<tarih>-<saat>.json")
    args = parser.parse_args()

    if any(c < 1 for c in args.concurrency):
        parser.error("--concurrency en az 1 olmalı.")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit en az 1 olmalı.")
    if args.pause and args.concurrency != [1]:
        parser.error("--pause yalnızca --concurrency 1 ile kullanılır.")

    #Parola ve auth ayarı uygulamanın okuduğu .env'den
    from core.settings import get_settings
    settings = get_settings()

    questions = pick_questions(args.question_set, args.limit)
    run_id = "load-" + time.strftime("%H%M%S")
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    levels = []

    #CPU sayaçları okunamazsa uyarı verilir, test CPU bilgisi olmadan sürer
    container = args.container
    omp_threads = None
    if container:
        if read_cpu_stat(container) is None:
            print(f"UYARI: {container} container'ının CPU sayaçları okunamadı (docker exec); CPU bilgisi olmadan devam ediliyor.")
            container = None
        else:
            omp_threads = (docker_exec(container, "printenv", "OMP_NUM_THREADS") or "").strip() or "tanımsız"

    with httpx.Client(base_url=args.base_url, timeout=httpx.Timeout(args.timeout, connect=10)) as client:
        check_ready(client)
        login(client, None if settings.app_password is None else settings.app_password.get_secret_value(), settings.auth_enabled)
        kinds = Counter(q["kind"] for q in questions)
        print(f"{len(questions)} soru ({dict(kinds)}), eşzamanlılık {args.concurrency}, koşu {run_id}"
              + (f", OMP_NUM_THREADS={omp_threads}" if omp_threads else ""))
        for concurrency in args.concurrency:
            print(f"\nEşzamanlılık {concurrency} başlıyor...")
            before = read_cpu_stat(container)
            results, wall = run_level(client, questions, concurrency, run_id, args.pause)
            cpu = cpu_delta(before, read_cpu_stat(container), wall)
            summary = summarize(results, wall, args.pause, cpu)
            print(format_summary(concurrency, summary))
            levels.append({"concurrency": concurrency, "summary": summary, "results": results})

    print("\nÖZET")
    for level in levels:
        print(format_summary(level["concurrency"], level["summary"]))

    out = args.out or RESULTS_DIR / f"load-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    report = {"run_id": run_id, "started_utc": started,
              "base_url": args.base_url, "set": args.question_set, "limit": args.limit, "pause": args.pause,
              "concurrency": args.concurrency, "timeout": args.timeout,
              "container": container, "omp_num_threads": omp_threads, "levels": levels}
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSonuçlar yazıldı: {out}")


if __name__ == "__main__":
    main()
