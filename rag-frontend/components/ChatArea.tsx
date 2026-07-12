'use client';
import { useEffect, useRef } from "react";
import { Menu, ArrowUp, Paperclip, Loader2 } from "lucide-react";

//Type safety
interface Message {
  role: 'user' | 'assistant';
  text: string;
  resources?: Array<{
    source_name: string;
    page: string;
    score: number;
    raw_text: string;
  }>;
  total_token_usage?: number;
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
  uploadProgress: number;
  setSelectedDoc?: (doc: string | null) => void;
  isLoading: boolean;
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
  uploadProgress,
  setSelectedDoc,
  isLoading // <-- Parametre olarak buraya ekledik
}: ChatAreaProps) {
  
  //Chat area scroll
  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isLoading]); // isLoading değiştiğinde de aşağı kaysın

  const lastAssistantMessage = [...messages].reverse().find(msg => msg.role === 'assistant');
  const currentTokenUsage = lastAssistantMessage?.total_token_usage || 0;

  return (
    
    <section className="flex-1 flex flex-col min-w-0 bg-slate-300 h-full relative border-r border-slate-200">
      
      {!isSidebarOpen && (
        <div className="absolute top-5 left-5 z-50"> 
          <button onClick={() => setIsSidebarOpen(true)} className="p-2 rounded-xl bg-white backdrop-blur-md border border-slate-200 text-slate-600 hover:bg-slate-200/60 shadow-sm transition-all">
            <Menu className="h-4 w-4" />
          </button>
        </div>
      )}

      {/* TOKEN USAGE */}
      <div className="absolute top-5 right-6 z-30 text-[10px] text-slate-400 font-semibold tracking-wide">
        Token Usage: {currentTokenUsage.toLocaleString()} / 10K
      </div>
      
      {/* MESAJ ALANI */}
      <div className="flex-1 overflow-y-auto px-0 relative">
        
        <div className="sticky top-0 w-full h-24 bg-gradient-to-b from-slate-200 via-slate-200 to-transparent pointer-events-none z-20 backdrop-blur-[3px]"></div>
      
        <div className="max-w-3xl w-full mx-auto pl-3 space-y-5 -mt-24 pt-28 pb-6 flex flex-col">
          {messages.map((msg, index) => (
            <div 
              key={index} 
              className={`flex flex-col ${msg.role === 'user' ? 'items-end' : 'items-start'}`}
            >
              <div 
                className={`max-w-[80%] w-max rounded-2xl px-4 py-3 text-xs leading-relaxed font-medium shadow-sm ${
                  msg.role === 'user' 
                    ? 'bg-indigo-600 text-white rounded-br-none' 
                    : 'bg-white text-slate-800 border border-slate-200/60 rounded-bl-none'
                }`}
              >
                {msg.text}
              </div>

              {/* KULLANILAN KAYNAKLAR*/}
              {msg.role === 'assistant' && msg.resources && msg.resources.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-2 max-w-[80%] animate-in fade-in slide-in-from-bottom-1 duration-200">
                  <span className="text-[10px] text-slate-400 font-semibold block w-full pl-1">
                    Kullanılan Kaynaklar:
                  </span>
                  {msg.resources.map((res, i) => (
                    <button 
                      key={i}
                      onClick={() => {
                        if (setSelectedDoc) {
                          setSelectedDoc(res.source_name);
                          console.log("Kaynak seçildi.")
                        }
                      }}
                      className="bg-indigo-50/60 hover:bg-indigo-100/80 border border-indigo-100 text-[11px] text-indigo-600 font-semibold px-3 py-1 rounded-full transition-all flex items-center gap-1.5 active:scale-95 group shadow-sm"
                      title={res.raw_text}
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
              <div className="max-w-[80%] w-max rounded-2xl px-4 py-3 text-xs font-medium bg-white/80 text-slate-500 border border-slate-200/60 rounded-bl-none shadow-sm flex items-center gap-2">
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
        
        {/*İlerleme Çubuğu */}
        {isUploading && (
          <div className="max-w-3xl mx-auto bg-white border border-slate-200 p-3.5 rounded-xl flex flex-col gap-2 shadow-sm">
            <div className="flex items-center justify-between text-xs text-slate-600 font-medium">
              <div className="flex items-center gap-2">
                <Loader2 className="h-3.5 w-3.5 animate-spin text-indigo-500" />
                <span>Dosya işleniyor...</span>
              </div>
              <span className="font-bold text-indigo-600">%{uploadProgress}</span>
            </div>
            <div className="w-full bg-slate-100 h-1.5 rounded-full overflow-hidden">
              <div 
                className="bg-indigo-600 h-full transition-all duration-300 ease-out rounded-full"
                style={{ width: `${uploadProgress}%` }}
              />
            </div>
          </div>
        )}

        {/* INPUT KUTUSU */}
        <div className="max-w-3xl mx-auto">
          <div className="flex-1 flex items-center bg-white border border-slate-200 rounded-xl px-3 relative h-14 shadow-sm focus-within:ring-2 focus-within:ring-indigo-500/10 focus-within:border-indigo-500 transition-all">
            
            <label className="flex h-9 w-9 items-center justify-center text-slate-400 hover:text-slate-600 cursor-pointer shrink-0 transition-colors">
              <input type="file" accept=".pdf" className="hidden" onChange={onFileUpload} disabled={isUploading} />
              <Paperclip className="w-5 h-5" />
            </label>
            
            <input 
              type="text" 
              value={input} 
              onChange={(e) => setInput(e.target.value)} 
              onKeyDown={(e) => e.key === 'Enter' && onSend()} 
              placeholder="Ask a question..." 
              className="flex-1 bg-transparent border-0 pl-2 pr-12 py-4 text-xs font-medium text-slate-700 placeholder-slate-400 focus:outline-none h-full" 
            />

            <div className="absolute right-2 top-1/2 -translate-y-1/2 w-10 h-10 flex items-center justify-center">
              {input.length > 0 && (
                <button 
                  onClick={onSend} 
                  className="rounded-lg bg-white hover:bg-slate-200 text-slate-900 p-2.5 h-10 w-10 flex items-center justify-center transition-all shadow-md shadow-indigo-100 animate-in fade-in zoom-in-95 duration-150"
                >
                  <ArrowUp className="w-4 h-4 shrink-0" />
                </button>
              )}
            </div>

          </div>
        </div>
      </div>
    </section>
  );
}