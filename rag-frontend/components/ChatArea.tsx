'use client';
import { useEffect, useRef, useState } from "react";
import { Menu, ArrowUp, Paperclip, Loader2 } from "lucide-react";
import type { Source, UsageStatus } from "@/types/api";
import type { Message } from "@/types/chat";
import MarkdownMessage from "@/components/MarkdownMessage";

//Backenddeki soru sınırıyla aynı (main.py MAX_QUESTION_LENGTH)
const MAX_QUESTION_LENGTH = 500;

//Aynı dosya ve sayfadan birden fazla parça gelirse tek rozet gösterilir (notes.md #31); parçaların metinleri
//rozetin ipucunda (title) alt alta durur. Sıra korunur: ilk rozet en alakalı parçanın sayfası
//key: "dosya#sayfa" tekilleştirmeden sonra benzersiz, liste sırası değişse de rozet doğru takip edilir (notes.md #35)
function groupSources(sources: Source[]) {
  const groups = new Map<string, { source_name: string; page: number; texts: string[] }>();
  for (const source of sources) {
    const key = `${source.source_name}#${source.page}`;
    const group = groups.get(key);
    if (group) {
      group.texts.push(source.raw_text);
    } else {
      groups.set(key, { source_name: source.source_name, page: source.page, texts: [source.raw_text] });
    }
  }
  return [...groups.entries()].map(([key, group]) => ({ key, ...group }));
}

//Kalan süreyi saat gibi yazar: 02:14:35
function formatCountdown(ms: number) {
  const total = Math.max(Math.ceil(ms / 1000), 0);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(Math.floor(total / 3600))}:${pad(Math.floor((total % 3600) / 60))}:${pad(total % 60)}`;
}

//Sınır doluyken input'un üstünde görünür; geri sayım saniyede bir güncellenir
//Kilidin açılması page.tsx'te: sıfırlanma anında kullanım yeniden çekiliyor
function TokenLimitNotice({ resetsAt }: { resetsAt: string }) {
  const [now, setNow] = useState(Date.now);

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);

  const resetDate = new Date(resetsAt);
  //Sınır Türkiye saatiyle sıfırlanıyor; saati de Türkiye saatiyle gösteriyoruz
  const resetClock = resetDate.toLocaleTimeString("tr-TR", { hour: "2-digit", minute: "2-digit", timeZone: "Europe/Istanbul" });

  return (
    <div className="max-w-3xl mx-auto bg-amber-50 border border-amber-200 p-3.5 rounded-xl flex items-center justify-between gap-3 text-xs font-medium text-amber-800 shadow-sm">
      <span>Token kullanım sınırına ulaşıldı. Sıfırlanma saati: {resetClock}</span>
      <span className="font-bold tabular-nums">{formatCountdown(resetDate.getTime() - now)}</span>
    </div>
  );
}

//Props type safety
interface ChatAreaProps {
  messages: Message[];
  isSidebarOpen: boolean;
  setIsSidebarOpen: (open: boolean) => void;
  input: string;
  setInput: (val: string) => void;
  onSend: () => void;
  onFileUpload: (e: React.ChangeEvent<HTMLInputElement>) => void;
  isUploading: boolean;
  //Kaynak rozetine tıklanınca dokümanı o sayfada açar
  onOpenSource?: (name: string, page: number) => void;
  isLoading: boolean;
  usage: UsageStatus | null;
}

//Props
export default function ChatArea({ 
  messages, 
  isSidebarOpen, 
  setIsSidebarOpen,
  input,
  setInput,
  onSend,
  onFileUpload,
  isUploading,
  onOpenSource,
  isLoading, // <-- Parametre olarak buraya ekledik
  usage
}: ChatAreaProps) {
  
  //Chat area scroll
  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isLoading]); // isLoading değiştiğinde de aşağı kaysın

  //Soru kutusu yazdıkça büyüsün; üst sınırı max-h-36 belirliyor, sonrasında içeride kayar
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${el.scrollHeight}px`;
  }, [input]); // gönderimden sonra input boşalınca tek satıra döner

  //Token sınırı doluyken soru kutusu ve gönder butonu kapalı
  const isLimited = usage?.limited ?? false;

  return (
    
    <section className="flex-1 flex flex-col min-w-0 bg-slate-300 h-full relative border-r border-slate-200">
      
      {!isSidebarOpen && (
        <div className="absolute top-5 left-5 z-50"> 
          <button onClick={() => setIsSidebarOpen(true)} className="p-2 rounded-xl bg-white backdrop-blur-md border border-slate-200 text-slate-600 hover:bg-slate-200/60 shadow-sm transition-all">
            <Menu className="h-4 w-4" />
          </button>
        </div>
      )}

      {/* TOKEN KULLANIMI */}
      {/* "tr-TR": binlik ayırıcı nokta (1.234); verilmezse tarayıcının diline göre 1,234 de çıkabiliyordu */}
      <div className="absolute top-5 right-6 z-30 text-[10px] text-black font-semibold tracking-wide">
        Token kullanımı: {usage ? `${usage.used.toLocaleString("tr-TR")} / ${usage.limit.toLocaleString("tr-TR")}` : '-'}
      </div>
      
      {/* MESAJ ALANI */}
      <div className="flex-1 overflow-y-auto px-0 relative">
        
        <div className="sticky top-0 w-full h-24 bg-gradient-to-b from-slate-200 via-slate-200 to-transparent pointer-events-none z-20 backdrop-blur-[3px]"></div>
      
        <div className="max-w-3xl w-full mx-auto pl-3 space-y-5 -mt-24 pt-28 pb-6 flex flex-col">
          {/* key={index} burada bilinçli: mesajlar yalnızca sona ekleniyor, hiç silinmiyor veya yer değiştirmiyor, */}
          {/* bu yüzden sıra numarası her zaman aynı mesajı gösteriyor. Silme/düzenleme eklenirse kimlik verilmeli (#35) */}
          {messages.map((msg, index) => (
            <div
              key={index}
              className={`flex flex-col ${msg.role === 'user' ? 'items-end' : 'items-start'}`}
            >
              <div 
                className={`max-w-[80%] w-max rounded-2xl px-4 py-3 text-xs leading-relaxed font-medium shadow-sm ${
                  msg.role === 'user' 
                    ? 'bg-indigo-600 text-white rounded-br-none' 
                    : 'bg-white text-black border border-slate-200/60 rounded-bl-none'
                }`}
              >
                {/* Asistan cevabı markdown (kalın, liste); kullanıcının sorusu düz metin (notes.md #32) */}
                {msg.role === 'assistant' ? <MarkdownMessage text={msg.text} /> : msg.text}
              </div>

              {/* KULLANILAN KAYNAKLAR*/}
              {msg.role === 'assistant' && msg.sources && msg.sources.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-2 max-w-[80%] animate-in fade-in slide-in-from-bottom-1 duration-200">
                  <span className="text-[10px] text-black font-semibold block w-full pl-1">
                    Kullanılan Kaynaklar:
                  </span>
                  {groupSources(msg.sources).map((res) => (
                    <button
                      key={res.key}
                      onClick={() => onOpenSource?.(res.source_name, res.page)}
                      className="bg-indigo-50/60 hover:bg-indigo-100/80 border border-indigo-100 text-[11px] text-indigo-600 font-semibold px-3 py-1 rounded-full transition-all flex items-center gap-1.5 active:scale-95 group shadow-sm"
                      title={res.texts.join("\n\n---\n\n")}
                    >
                      <span className="w-1.5 h-1.5 rounded-full bg-indigo-500 group-hover:animate-pulse" />
                      <span>{res.source_name} • Sayfa {res.page}</span>
                    </button>
                  ))}
                </div>
              )}

            </div>
          ))}

          {/* LLM YANIT YÜKLENİYOR ANIMASYONU */}
          {isLoading && (
            <div className="flex flex-col items-start animate-in fade-in duration-200">
              <div className="max-w-[80%] w-max rounded-2xl px-4 py-3 text-xs font-medium bg-white/80 text-black border border-slate-200/60 rounded-bl-none shadow-sm flex items-center gap-2">
                <Loader2 className="h-3.5 w-3.5 animate-spin text-indigo-500" />
                <span>Yanıt üretiliyor...</span>
              </div>
            </div>
          )}

          <div ref={messagesEndRef} />
        </div>
      </div>
      
      {/* INPUT VE DOSYA YÜKLEME ALANI */}
      <div className="p-6 shrink-0 bg-transparent space-y-4">
        
        {/* YÜKLEME GÖSTERGESİ (belirsiz / indeterminate) */}
        {/* Yüzde yok: sürenin çoğu backend'deki embedding, gerçek ilerleme bilinmiyor; sahte yüzde yerine kayan çubuk (notes.md #30) */}
        {/* role="status": ekran okuyucu "Dosya işleniyor" duyurur; aria-valuenow olmayan progressbar "belirsiz" demek */}
        {isUploading && (
          <div role="status" className="max-w-3xl mx-auto bg-white border border-slate-200 p-3.5 rounded-xl flex flex-col gap-2 shadow-sm">
            <div className="flex items-center gap-2 text-xs text-black font-medium">
              <Loader2 className="h-3.5 w-3.5 animate-spin text-indigo-500" />
              <span>Dosya işleniyor... Büyük PDF&apos;lerde bu birkaç dakika sürebilir.</span>
            </div>
            <div
              role="progressbar"
              aria-label="Dosya işleniyor"
              className="relative w-full bg-slate-100 h-1.5 rounded-full overflow-hidden"
            >
              <div className="absolute inset-y-0 left-0 w-1/3 bg-indigo-600 rounded-full animate-indeterminate motion-reduce:animate-none motion-reduce:w-full motion-reduce:opacity-40" />
            </div>
          </div>
        )}

        {/* TOKEN SINIRI UYARISI */}
        {isLimited && usage && <TokenLimitNotice resetsAt={usage.resets_at} />}

        {/* INPUT KUTUSU */}
        <div className="max-w-3xl mx-auto">
          <div className="flex-1 flex items-end bg-white border border-slate-200 rounded-xl px-3 py-2.5 relative min-h-14 shadow-sm focus-within:ring-2 focus-within:ring-indigo-500/10 focus-within:border-indigo-500 transition-all">
            
            <label className="flex h-9 w-9 items-center justify-center text-slate-400 hover:text-slate-600 cursor-pointer shrink-0 transition-colors">
              <input type="file" accept=".pdf" className="hidden" onChange={onFileUpload} disabled={isUploading} />
              <Paperclip className="w-5 h-5" />
            </label>
            
            {/* Enter gönderir, Shift+Enter yeni satır ekler; IME ile harf birleştirilirken basılan Enter göndermez */}
            <textarea
              ref={textareaRef}
              rows={1}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                  e.preventDefault();
                  onSend();
                }
              }}
              maxLength={MAX_QUESTION_LENGTH}
              disabled={isLimited}
              placeholder={isLimited ? "Token limiti doldu" : "Dokümanlarınız hakkında bir soru sorun..."}
              className="flex-1 resize-none bg-transparent border-0 pl-2 pr-12 py-2.5 text-xs font-medium text-black placeholder-slate-400 focus:outline-none max-h-36 overflow-y-auto disabled:cursor-not-allowed"
            />

            {/* Buton her zaman yerinde; gönderilemeyecekken pasif görünür (notes.md #37). Eskiden kutu boşken */}
            {/* kayboluyordu, yazmaya başlayınca beliriyordu. Yalnızca boşluk yazılmışsa da gönderilmez (handleSend) */}
            <div className="absolute right-2 bottom-2 w-10 h-10 flex items-center justify-center">
              <button
                onClick={onSend}
                disabled={!input.trim() || isLoading || isLimited}
                aria-label="Gönder"
                title="Gönder"
                className="rounded-lg bg-white hover:bg-slate-200 text-slate-900 p-2.5 h-10 w-10 flex items-center justify-center transition-all shadow-md shadow-indigo-100 disabled:opacity-40 disabled:cursor-not-allowed disabled:shadow-none disabled:hover:bg-white"
              >
                <ArrowUp className="w-4 h-4 shrink-0" />
              </button>
            </div>

          </div>

          {/* KARAKTER SAYACI: yazmaya başlayınca görünür, sınıra yaklaşınca renk değiştirir */}
          {input.length > 0 && (
            <div className={`mt-1.5 pr-1 text-right text-[10px] font-semibold ${
              input.length >= MAX_QUESTION_LENGTH
                ? 'text-red-500'
                : input.length >= MAX_QUESTION_LENGTH * 0.9
                  ? 'text-amber-500'
                  : 'text-black'
            }`}>
              {input.length}/{MAX_QUESTION_LENGTH}
            </div>
          )}
        </div>
      </div>
    </section>
  );
}