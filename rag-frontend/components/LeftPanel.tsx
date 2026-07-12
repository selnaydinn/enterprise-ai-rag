'use client';
import { Search, ChevronLeft, FileText, LogOut } from "lucide-react";
import Image from 'next/image';

interface DocumentItem {
  name: string;
}

//Props type safety
interface LeftPanelProps {
  isSidebarOpen: boolean;
  setIsSidebarOpen: (open: boolean) => void;
  selectedDoc: string | null;
  setSelectedDoc: (docName: string | null) => void;
  searchTerm: string;
  setSearchTerm: (term: string) => void;
  filteredDocuments: DocumentItem[];
}

//Props
export default function LeftPanel({ 
  isSidebarOpen, 
  setIsSidebarOpen, 
  selectedDoc, 
  setSelectedDoc, 
  searchTerm, 
  setSearchTerm, 
  filteredDocuments 
}: LeftPanelProps) {
  return (
    <aside className={`bg-slate-50 flex flex-col justify-between h-full transition-all duration-300 ease-in-out overflow-hidden shrink-0 ${
      isSidebarOpen 
        ? 'w-80 p-6' 
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
                alt="Enterprise AI Logo"
                fill                     
                className="object-contain" 
                priority                 
              />
            </div>

            {/* BAŞLIK VE METİNLER */}
            <div className="flex flex-col">
              <h1 className="text-lg font-bold text-slate-900 tracking-tight leading-none">
                Enterprise AI
              </h1>
              <span className="text-[10px] text-slate-400 font-medium mt-1">
                Search Among Company Files
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
            type="text"
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            placeholder="Search documents..."
            className="w-full bg-transparent border-0 p-0 text-xs text-slate-700 placeholder-slate-400 focus:outline-none"
          />
        </div>

        {/* DÖKÜMAN LİSTESİ */}
        <div className="flex-1 overflow-y-auto pr-1">
          <div className="space-y-0.5">
            {filteredDocuments && filteredDocuments.length > 0 ? (
              filteredDocuments.map((doc, idx) => {
                const isSelected = selectedDoc === doc.name;
                return (
                  <div 
                    key={idx} 
                    onClick={() => setSelectedDoc(doc.name)} 
                    className={`flex items-center gap-2.5 px-3 py-2 rounded-lg text-xs font-medium cursor-pointer transition-all ${
                      isSelected 
                        ? 'bg-slate-200/60 text-slate-900 font-semibold shadow-sm' 
                        : 'text-slate-500 hover:text-slate-800 hover:bg-slate-200/30'
                    }`}
                  >
                    <FileText className={`h-3.5 w-3.5 shrink-0 ${isSelected ? 'text-slate-700' : 'text-slate-400'}`} />
                    <span className="truncate">{doc.name}</span>
                  </div>
                );
              })
            ) : (
              <p className="text-center text-xs text-slate-400 py-6 font-medium">No documents found.</p>
            )}
          </div>
        </div>

        {/* KULLANICI PROFİL VE ÇIKIŞ ALANI */}
        <div className="pt-4 border-t border-slate-200 flex items-center justify-between shrink-0">
          <div className="flex items-center gap-3 min-w-0">
            <div className="h-8 w-8 rounded-full bg-slate-200 text-slate-600 flex items-center justify-center font-bold text-xs shrink-0">
              SA
            </div>
            <div className="flex flex-col min-w-0">
              <span className="text-xs font-semibold text-slate-700 truncate">Selin Aydın</span>
              <span className="text-[10px] text-slate-400 font-medium truncate">selin@company.com</span>
            </div>
          </div>
          
          {/* Çıkış Butonu */}
          <button 
            onClick={() => alert("Göstermelik çıkış yapıldı.")} 
            className="p-1.5 text-slate-400 hover:text-red-600 hover:bg-slate-100 rounded-lg transition-all"
            title="Sign Out"
          >
            <LogOut className="h-4 w-4" />
          </button>
        </div>

      </div>
    </aside>
  );
}