'use client';
import { useRef } from "react";
import { Search, ChevronLeft, FileText, LogOut, Trash2 } from "lucide-react";
import Image from 'next/image';
import type { DocumentItem } from "@/types/api";

//Props type safety
interface LeftPanelProps {
  isSidebarOpen: boolean;
  setIsSidebarOpen: (open: boolean) => void;
  selectedDoc: string | null;
  setSelectedDoc: (docName: string | null) => void;
  searchTerm: string;
  setSearchTerm: (term: string) => void;
  filteredDocuments: DocumentItem[];
  //Verilmezse (backend'de silme kapalıysa) silme butonu görünmez
  onDeleteDocument?: (docName: string) => void;
  //Backend'de oturumu kapatır (Workspace.tsx handleSignOut), ardından giriş ekranına dönülür
  onSignOut: () => void;
}

//Props
export default function LeftPanel({ 
  isSidebarOpen, 
  setIsSidebarOpen, 
  selectedDoc, 
  setSelectedDoc, 
  searchTerm, 
  setSearchTerm,
  filteredDocuments,
  onDeleteDocument,
  onSignOut
}: LeftPanelProps) {

  //KLAVYEYLE DOKÜMAN SEÇME (notes.md #36): Tab ile listeye tek adımda girilir, ↑ ↓ ile gezilir, Enter ile açılır
  //"Roving tabindex": satırlar buton ama yalnızca biri Tab sırasında (tabIndex 0), diğerleri -1; aralarında oklarla
  //geziliyor. Tab sırasındaki satır seçili doküman, yoksa ilki. Oklarla gezmek PDF'i açmaz; açmak için Enter
  const searchRef = useRef<HTMLInputElement>(null);
  const itemRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const selectedIndex = filteredDocuments.findIndex((doc) => doc.name === selectedDoc);
  const tabbableIndex = selectedIndex >= 0 ? selectedIndex : 0;

  //↓ sonraki, ↑ önceki doküman; listenin sonunda durur, en üstte ↑ arama kutusuna döner
  const handleItemKeyDown = (e: React.KeyboardEvent<HTMLButtonElement>, index: number) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      itemRefs.current[Math.min(index + 1, filteredDocuments.length - 1)]?.focus();
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      if (index === 0) {
        searchRef.current?.focus();
      } else {
        itemRefs.current[index - 1]?.focus();
      }
    }
  };

  //Arama kutusunda ↓: listedeki ilk dokümana geçer (ara → okla seç → Enter)
  const handleSearchKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown" && filteredDocuments.length > 0) {
      e.preventDefault();
      itemRefs.current[0]?.focus();
    }
  };

  return (
    <aside className={`bg-slate-50 flex flex-col justify-between h-full transition-all duration-300 ease-in-out overflow-hidden shrink-0 ${
      isSidebarOpen 
        ? 'w-64 p-6'
        : 'w-0 p-0 invisible'
    }`}>
      
      {/* ÜST VE ORTA KISIM İÇİN TEK BİR ANA TAŞIYICI */}
      <div className="flex flex-col h-full overflow-hidden space-y-8">
        
        
        <div className="flex items-start justify-between w-full pt-4 ">
          
          {/* Sol Taraf: Logo ve Metinler */}
          <div className="flex items-center gap-3">
          
            <div className="relative w-12 h-12 shrink-0"> 
              <Image 
                src="/logo.png"            
                alt="Enterprise AI logosu"
                fill                     
                className="object-contain" 
                priority                 
              />
            </div>

            {/* BAŞLIK VE METİNLER */}
            <div className="flex flex-col">
              <h1 className="text-lg font-bold text-black tracking-tight leading-none">
                Enterprise AI
              </h1>
              <span className="text-[10px] text-black font-medium mt-1">
                Kurum Dokümanlarında Arama
              </span>
            </div>
          </div>

          
          <button 
            onClick={() => setIsSidebarOpen(false)} 
            className="p-1.5 rounded-lg hover:bg-slate-200/60 text-slate-400 hover:text-slate-600 transition-colors mt-0.5"
          >
            <ChevronLeft className="h-4 w-4" />
          </button>

        </div>

        {/*ARAMA ÇUBUĞU */}
        <div className="flex items-center bg-transparent px-2 pb-2 border-b border-slate-200 focus-within:border-slate-400 transition-all">
          <Search className="h-3.5 w-3.5 text-slate-400 mr-2 shrink-0" />
          <input
            ref={searchRef}
            type="text"
            onKeyDown={handleSearchKeyDown}
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            placeholder="Doküman ara..."
            className="w-full bg-transparent border-0 p-0 text-xs text-black placeholder-slate-400 focus:outline-none"
          />
        </div>

        {/* DÖKÜMAN LİSTESİ */}
        <div className="flex-1 overflow-y-auto pr-1">
          {filteredDocuments && filteredDocuments.length > 0 ? (
            <ul className="space-y-0.5">
              {filteredDocuments.map((doc, index) => {
                const isSelected = selectedDoc === doc.name;
                return (
                  //key: dosya adı (backend'de benzersiz); sıra numarası olsaydı bir doküman silinince ya da arama
                  //filtresi değişince React kalan öğeleri yanlış eşleştirirdi (notes.md #35)
                  //Satır iki yan yana buton: aç ve sil (buton buton içine konamaz)
                  <li
                    key={doc.name}
                    className={`flex items-center pr-3 rounded-lg text-xs font-medium transition-all ${
                      isSelected
                        ? 'bg-slate-200/60 text-black font-semibold shadow-sm'
                        : 'text-black hover:bg-slate-200/30'
                    }`}
                  >
                    <button
                      ref={(el) => { itemRefs.current[index] = el; }}
                      tabIndex={index === tabbableIndex ? 0 : -1}
                      onClick={() => setSelectedDoc(doc.name)}
                      onKeyDown={(e) => handleItemKeyDown(e, index)}
                      aria-current={isSelected ? "true" : undefined}
                      className="flex flex-1 min-w-0 items-center gap-2.5 pl-3 py-2 text-left rounded-lg cursor-pointer focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400"
                    >
                      <FileText className={`h-3.5 w-3.5 shrink-0 ${isSelected ? 'text-slate-700' : 'text-slate-400'}`} />
                      <span className="truncate flex-1">{doc.name}</span>
                    </button>
                    {/* Silme yalnızca fareyle: Tab sırasının dışında (tabIndex -1) */}
                    {onDeleteDocument && (
                      <button
                        tabIndex={-1}
                        onClick={() => onDeleteDocument(doc.name)}
                        className="ml-2.5 p-1 rounded text-slate-400 hover:text-red-600 hover:bg-slate-200/60 transition-colors shrink-0"
                        title="Dokümanı sil"
                        aria-label={`${doc.name} dokümanını sil`}
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    )}
                  </li>
                );
              })}
            </ul>
          ) : (
            //Hiç doküman yok ile arama eşleşmedi ayrı durumlar; kullanıcı ikincisinde aramayı değiştirmeli
            <p className="text-center text-xs text-black py-6 font-medium">
              {searchTerm.trim() ? "Aramayla eşleşen doküman yok." : "Henüz doküman yüklenmedi."}
            </p>
          )}
        </div>

        {/* KULLANICI PROFİL VE ÇIKIŞ ALANI */}
        <div className="pt-4 border-t border-slate-200 flex items-center justify-between shrink-0">
          <div className="flex items-center gap-3 min-w-0">
            <div className="h-8 w-8 rounded-full bg-slate-200 text-black flex items-center justify-center font-bold text-xs shrink-0">
              Y
            </div>
            {/* Tek kullanıcı (parola ile giriş); kullanıcı adı/e-posta yok */}
            <div className="flex flex-col min-w-0">
              <span className="text-xs font-semibold text-black truncate">Yönetici</span>
              <span className="text-[10px] text-black font-medium truncate">Oturum açık</span>
            </div>
          </div>

          {/* Çıkış Butonu */}
          <button
            onClick={onSignOut}
            className="p-1.5 text-slate-400 hover:text-red-600 hover:bg-slate-100 rounded-lg transition-all"
            title="Çıkış yap"
            aria-label="Çıkış yap"
          >
            <LogOut className="h-4 w-4" />
          </button>
        </div>

      </div>
    </aside>
  );
}