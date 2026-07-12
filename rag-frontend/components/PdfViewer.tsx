'use client';
import { X, FileText } from "lucide-react";

//Prop type safety
interface PdfViewerProps {
  selectedDoc: string | null; // Örn: http://localhost:8000/static-pdfs/meb_yonetmelik.pdf
  setSelectedDoc: (docName: string | null) => void;
}

//Props
export default function PdfViewer({ selectedDoc, setSelectedDoc }: PdfViewerProps) {
  
  // Dosya adını URL'den ayıklayıp sadece temiz başlığı göstermek için (Header için)
  const getFileName = (url: string | null) => {
    if (!url) return "";
    const parts = url.split('/');
    return decodeURIComponent(parts[parts.length - 1]);
  };

  return (
    <section className={`h-full bg-slate-50/90 backdrop-blur-md transition-all duration-300 overflow-hidden flex flex-col shrink-0 ${
      selectedDoc ? 'w-[45%] border-l border-slate-200/60 shadow-sm' : 'w-0'
    }`}>
        {selectedDoc && (
          <>
            {/* ÜST HEADER ALANI*/}
            <div className="flex items-center justify-between p-4 border-b border-slate-200 bg-white">
              <span className="text-xs text-slate-700 truncate font-semibold max-w-[85%]">
                {getFileName(selectedDoc)}
              </span>
              <button 
                onClick={() => setSelectedDoc(null)} 
                className="p-1.5 hover:bg-slate-100 text-slate-400 hover:text-slate-600 rounded-lg transition-colors"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
            
            {/*PDF GÖSTERİM ALANI */}
            <div className="flex-1 w-full h-full bg-slate-100/50 relative">
              <iframe
                src={`${selectedDoc}#toolbar=1&navpanes=0`} 
                className="w-full h-full border-0 bg-white"
                title="PDF Viewer"
              />
            </div>

            {/* KÜÇÜK BİLGİLENDİRME FOOTER'I */}
            <div className="p-3 border-t border-slate-200 bg-white flex items-center gap-2 justify-center select-none text-[11px] text-slate-400 font-medium">
              <FileText className="h-3.5 w-3.5 text-indigo-500/70" />
              <span>Doküman Önizleme Modu</span>
            </div>
          </>
        )}
      </section>
  );
}