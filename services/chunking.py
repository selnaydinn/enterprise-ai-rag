#PDF sayfalarını chunk'lara bölme yöntemleri (services/ingestion.py prepare_chunks kullanıyor).
#Saf bir işlem: sayfa Document'ları girer, chunk Document'ları çıkar; PDF okumaz, DB'ye dokunmaz.
#Yöntem değişirse DB yeniden kurulmalı (scripts/build_db.py --chunking); uygulamanın ayarı (chunking_strategy)
#DB'yi kuran yöntemle aynı olmalı, yoksa yeni yüklenen PDF'ler diğerlerinden farklı bölünür.

import logging
import re

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from core.settings import get_settings

logger = logging.getLogger("EnterpriseAI")

#"page": sayfa metni chunk_size / chunk_overlap karakterle bölünür (bugünkü DB'ler böyle kuruldu)
#"madde": metin "MADDE N" başlıklarından ve sayfa sınırlarından bölünür; her chunk tek bir maddenin tek bir sayfadaki
#kısmıdır, devam eden parçaların başına madde başlığı eklenir (notes.md madde 13, eval/experiment_A_results)
CHUNKING_STRATEGIES = ("page", "madde")

#Satır başında madde başlığı: "MADDE 36 –", "Madde 5-", "MADDE 63/B –", "GEÇİCİ MADDE 3 –", "EK MADDE 1 –"
#Tire bazen sonraki satırda ("Madde 20" / "—", AcilSaglikHizmetleri.pdf): satırda numaradan sonra bir şey yoksa da başlık
#Satır başı şartı metindeki "36 ncı maddenin" gibi atıfları dışarıda bırakır
ARTICLE_HEADING = re.compile(
    r"^\s*(?P<kind>GEÇİCİ\s+MADDE|Geçici\s+Madde|EK\s+MADDE|Ek\s+Madde|MADDE|Madde)\s+"
    r"(?P<num>\d+(?:\s*/\s*[A-ZÇĞİÖŞÜ])?)\s*(?:[-–—]|$)")
#Satır başında ek başlığı: "EK-1", "EK 3", "EK -5", "EK- 7", "EK-2 A"; ilk ekten sonra madde aranmaz (formlar, tablolar)
#Sınır: madde dışı kısım yalnızca bu başlıkla tanınır. Son maddeden sonra "EK" başlığı olmadan gelen tablo, form veya
#başka metin son maddenin devamı sayılır: madde yöntemiyle bölünür ve chunk'larının başına son maddenin başlığı eklenir
#(ör. "MADDE 41 – Yürütme"). Metin kaybolmaz ama yanlış maddeye bağlanır. Bugünkü PDF'lerde bir örneği var:
#KanMerkezleriYonetmelik.pdf s.6'nın başındaki "Yönetmelikte Değişiklik Yapan Yönetmelikler" tablosu EK-1'den önce
#geldiği için MADDE 25'in (Yürütme) devamı sayılıyor. Gerekirse "Yürürlük"/"Yürütme" maddesinden sonrası veya
#"TABLO"/"FORM" gibi başlıklar da ek başlangıcı sayılabilir
ANNEX_HEADING = re.compile(r"^\s*EK\s*[-–]?\s*\d")
#Fıkra başı: satır başında "(1)", "(2)"...; uzun maddeler bu sınırlardan bölünür
PARAGRAPH_START = re.compile(r"(?m)^(?=\s*\(\d+\))")
#Yalnızca değişiklik notu olan satırlar ("(Değişik başlık:RG-13/9/2014-29118)", taşan "29118)") başlık sayılmaz
AMENDMENT_LINE = re.compile(r"^\(?\s*(Değişik|Ek|Mülga)\b.*$|^[\d\s/.:\-RG]*\)\s*$")

#Bir dokümanın madde yöntemiyle bölünmesi için en az bu kadar madde başlığı ve büyük ölçüde artan numara gerekir;
#yoksa (rapor, kılavuz, tesadüfen "Madde 3 –" ile başlayan bir satır) doküman "page" yöntemiyle bölünür
MIN_ARTICLES = 3
MIN_INCREASING_RATIO = 0.8
#Başlık bloğu (bölüm başlığı + bölüm adı + madde başlığı + değişiklik notları) en fazla bu kadar dolu satır; boş satırlar
#sayılmaz (bölüm başlarında blok boş satırlarla uzar; sayılırsa başlık bulunamıyordu)
MAX_TITLE_LINES = 6


def _page_splitter(chunk_size=None):
    settings = get_settings()
    return RecursiveCharacterTextSplitter(chunk_size=chunk_size or settings.chunk_size, chunk_overlap=settings.chunk_overlap)


#Sayfa Document'larını chunk'lara böler
def split_pages(pages, strategy):
    if strategy == "page":
        return _page_splitter().split_documents(pages)
    if strategy == "madde":
        return _split_by_article(pages)
    raise ValueError(f"Bilinmeyen chunking yöntemi: {strategy!r} (desteklenenler: {', '.join(CHUNKING_STRATEGIES)})")


#"İ".lower() "i" + birleşik nokta (U+0307) üretiyor; nokta atılmazsa "GEÇİCİ MADDE" eşleşmez ve "MADDE" etiketi alır
def _article_label(match) -> str:
    kind = re.sub(r"\s+", " ", match.group("kind")).strip().lower().replace("̇", "")
    prefix = {"geçici madde": "GEÇİCİ MADDE", "ek madde": "EK MADDE"}.get(kind, "MADDE")
    return f"{prefix} {re.sub(r'\s', '', match.group('num'))}"


#Başka bir maddenin başlık satırı (ör. içeriği yalnızca "(Mülga:RG-...)" olan madde) başlık bloğuna alınmaz
def _is_header_like(line: str) -> bool:
    s = line.strip()
    return bool(s) and len(s) <= 120 and not s[0].islower() and s[-1] not in ".,;:" and not ARTICLE_HEADING.match(s)


#Başlık satırının hemen önündeki başlık bloğunu (madde başlığı, bölüm başlığı, değişiklik notları) bulur ve
#lines'tan çıkarır; blok yeni maddeye taşınır. Bloğun önündeki satır cümle sonuyla bitmiyorsa (bölünmüş bir cümlenin
#parçası olabilir) hiçbir şey taşınmaz. Döner: (taşınan satırlar, madde başlığı veya "")
def _take_title_block(lines: list) -> tuple:
    j, filled = len(lines), 0
    while j > 0:
        line = lines[j - 1].strip()
        if not line:
            j -= 1
            continue
        if filled == MAX_TITLE_LINES or not (_is_header_like(line) or AMENDMENT_LINE.match(line)):
            break
        filled += 1
        j -= 1
    block = lines[j:]
    if not any(s.strip() for s in block):
        return [], ""
    if j > 0 and not lines[j - 1].rstrip().endswith((".", ":", ";", ")")):
        return [], ""
    del lines[j:]
    titles = [s.strip() for s in block if s.strip() and not AMENDMENT_LINE.match(s.strip()) and "BÖLÜM" not in s]
    return block, (titles[-1] if titles else "")


#Sayfaları bölümlere ayırır: her bölüm tek sayfada; tür "madde" (bir maddenin o sayfadaki kısmı), "ek" veya "other"
#(ilk maddeden önceki giriş). Madde sayfa sonunda bitmezse sonraki sayfada devam bölümü olarak sürer
def _segment(pages) -> list:
    segments, current, in_annex, seen_article = [], None, False, False

    def new(doc, kind, start=False, lines=None):
        return {"doc": doc, "kind": kind, "label": current[0] if current else None,
                "title": current[1] if current else "", "starts_article": start, "lines": lines or []}

    for doc in pages:
        seg = new(doc, "ek" if in_annex else ("madde" if current else "other"))
        for line in doc.page_content.split("\n"):
            if not in_annex and seen_article and ANNEX_HEADING.match(line):
                segments.append(seg)
                in_annex, current = True, None
                seg = new(doc, "ek")
            elif not in_annex and (m := ARTICLE_HEADING.match(line)):
                block, title = _take_title_block(seg["lines"])
                segments.append(seg)
                current, seen_article = (_article_label(m), title), True
                seg = new(doc, "madde", start=True, lines=block)
            seg["lines"].append(line)
        segments.append(seg)
    return _merge_heading_only([s for s in segments if "\n".join(s["lines"]).strip()])


#Başlığı sayfa sonuna denk gelen maddede o sayfada yalnızca başlık kalır ("Başhekimlik, Merkez ve Görevleri / MADDE 9 –");
#içeriksiz bu bölüm, maddenin sonraki sayfadaki devamıyla birleştirilir (chunk o sayfayı alır). Devamı olmayan
#bölümler (ör. içeriği yalnızca "(Mülga:...)" olan madde) olduğu gibi kalır
def _has_body(seg) -> bool:
    for line in seg["lines"]:
        s = line.strip()
        m = ARTICLE_HEADING.match(s)
        rest = s[m.end():].strip() if m else s
        if rest and not AMENDMENT_LINE.match(rest) and not (not m and _is_header_like(s)):
            return True
    return False


def _merge_heading_only(segments: list) -> list:
    merged = []
    for i, seg in enumerate(segments):
        nxt = segments[i + 1] if i + 1 < len(segments) else None
        if (seg["starts_article"] and not _has_body(seg) and nxt is not None and nxt["kind"] == "madde"
                and nxt["label"] == seg["label"] and not nxt["starts_article"]):
            nxt["lines"] = seg["lines"] + nxt["lines"]
            nxt["starts_article"] = True
            continue
        merged.append(seg)
    return merged


#Numaralar büyük ölçüde artıyor mu (geçici ve ek maddeler kendi numaralarıyla başladığı için yalnızca "MADDE" sayılır)
def _numbers_increase(labels: list) -> bool:
    nums = [int(re.match(r"MADDE (\d+)", label).group(1)) for label in labels if re.match(r"MADDE \d+", label)]
    if len(nums) < 2:
        return False
    pairs = list(zip(nums, nums[1:]))
    return sum(b >= a for a, b in pairs) / len(pairs) >= MIN_INCREASING_RATIO


#Bir maddenin bir sayfadaki kısmını chunk'lara böler. Sığıyorsa tek chunk; sığmıyorsa fıkra sınırlarından ("(2)")
#paketlenir, tek fıkra da sığmıyorsa karakterle bölünür. Maddenin başlığıyla başlamayan her chunk'ın başına başlık eklenir
def _split_article(seg) -> list:
    settings = get_settings()
    header = f"{seg['label']} – {seg['title']}" if seg["title"] else seg["label"]
    text = "\n".join(seg["lines"]).strip()
    metadata = {**seg["doc"].metadata, "article": seg["label"]}

    def with_header(body, first):
        return body if (first and seg["starts_article"]) else f"{header}\n{body}"

    if len(with_header(text, True)) <= settings.chunk_size:
        return [Document(page_content=with_header(text, True), metadata=dict(metadata))]

    #Bölünecekse başlık bloğu ve başlık satırı metinden çıkarılır (her chunk'ın başına zaten başlık eklenir); yoksa
    #karakterle bölmede başlık boş satırdan ayrılıp tek başına bir chunk olarak kalabiliyor ("... / MADDE 9 –")
    if seg["starts_article"]:
        lines = seg["lines"]
        k = next(i for i, line in enumerate(lines) if ARTICLE_HEADING.match(line))
        m = ARTICLE_HEADING.match(lines[k])
        text = "\n".join([lines[k][m.end():]] + lines[k + 1:]).strip()
        seg = {**seg, "starts_article": False}

    room = settings.chunk_size - len(header) - 1
    pieces = [p for p in PARAGRAPH_START.split(text) if p.strip()]
    bodies, current = [], ""
    for piece in pieces:
        if len(piece) > room:
            if current.strip():
                bodies.append(current)
                current = ""
            bodies.extend(_page_splitter(room).split_text(piece))
        elif len(current) + len(piece) <= room:
            current += piece
        else:
            bodies.append(current)
            current = piece
    if current.strip():
        bodies.append(current)
    return [Document(page_content=with_header(body.strip(), i == 0), metadata=dict(metadata))
            for i, body in enumerate(bodies) if body.strip()]


#"madde" yöntemi; yeterli madde başlığı yoksa doküman "page" yöntemiyle bölünür (bugünkü sonuçla aynı)
#Madde dışı bölümler (ilk maddeden önceki giriş, ekler) "page" yöntemiyle bölünür
def _split_by_article(pages) -> list:
    segments = _segment(pages)
    labels = [s["label"] for s in segments if s["starts_article"]]
    if len(labels) < MIN_ARTICLES or not _numbers_increase(labels):
        logger.info(f"Madde yapısı bulunamadı ({len(labels)} madde başlığı); page yöntemi kullanıldı.")
        return _page_splitter().split_documents(pages)

    chunks, splitter = [], _page_splitter()
    for seg in segments:
        if seg["kind"] == "madde":
            chunks.extend(_split_article(seg))
        else:
            text = "\n".join(seg["lines"]).strip()
            chunks.extend(splitter.split_documents([Document(page_content=text, metadata=dict(seg["doc"].metadata))]))
    annex = sum(s["kind"] == "ek" for s in segments)
    logger.info(f"Madde yöntemi: {len(labels)} madde, {len(chunks)} chunk" + (f", ekler page yöntemiyle ({annex} bölüm)" if annex else "") + ".")
    return chunks
