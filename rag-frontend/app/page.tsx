'use client';
import { useState, useEffect } from "react";
import ChatArea from "@/components/ChatArea";
import PdfViewer from "@/components/PdfViewer";
import LeftPanel from "@/components/LeftPanel";
import { apiService } from "@/services/api";

//Message Prop
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

//Document Prop
interface DocumentItem {
  name: string;
}

//Backenddeki upload sınırıyla aynı (main.py MAX_UPLOAD_BYTES)
const MAX_UPLOAD_BYTES = 20 * 1024 * 1024;

export default function Home() {

  const [messages, setMessages] = useState<Message[]>([
    { role: 'assistant', text: 'Merhaba! Kurumsal dokümanlarınız hakkında size nasıl yardımcı olabilirim?' }
  ]);

  const [input, setInput] = useState('');
  const [searchTerm, setSearchTerm] = useState('');
  const [isSidebarOpen, setIsSidebarOpen] = useState(true);
  
  const [isLoading, setIsLoading] = useState(false);
  
  const [selectedDoc, setSelectedDoc] = useState<string | null>(null);

  const [isUploading, setIsUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);

  const [documents, setDocuments] = useState<DocumentItem[]>([]);

  const filteredDocuments = documents.filter(doc => 
    doc.name.toLowerCase().includes(searchTerm.toLowerCase())
  );

  //Sayfa render edilirken dökümanları çektik
  useEffect(() => {
    const fetchExistingDocuments = async () => {
      try {
        const data = await apiService.getExistingDocuments();
        setDocuments(data);
        console.log("Mevcut dökümanlar apiService ile başarıyla yüklendi:", data);
      } catch (error) {
        console.error("Mevcut dökümanlar çekilirken bir hata oluştu:", error);
      }
    };

    fetchExistingDocuments();
  }, []); 

  //Event listener kullanarak upload butonunu dinliyoruz, backendi tetikliyoruz
  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const fileInput = e.target;
    const file = fileInput.files?.[0];
    if (!file) return;
    console.log("PDF yükleme işlemi başladı...");
    console.log("Seçilen Dosya:", file);

    //Seçicinin değerini sıfırlıyoruz; yoksa aynı dosya tekrar seçildiğinde onChange tetiklenmez
    //(file değişkeni dosyayı zaten tuttuğu için yükleme etkilenmez)
    fileInput.value = '';

    //Backend ".PDF" uzantısını da kabul ediyor
    if (!file.name.toLowerCase().endsWith('.pdf')) {
      alert("Lütfen sadece PDF dosyası yükleyin.");
      return;
    }

    //Büyük dosyayı göndermeden önce reddediyoruz; asıl kontrol backendde
    if (file.size > MAX_UPLOAD_BYTES) {
      alert(`Dosya boyutu ${MAX_UPLOAD_BYTES / (1024 * 1024)} MB sınırını aşıyor.`);
      return;
    }

    setIsUploading(true);
    setUploadProgress(10);

    const progressInterval = setInterval(() => {
      setUploadProgress((prev) => (prev >= 90 ? 90 : prev + 10));
    }, 250);

    try {
      const result = await apiService.uploadPdf(file);
      console.log("PDF yüklendi ve frontende yanıtını döndü.");
      clearInterval(progressInterval);

      //Backend nadiren 200 ile "Reddedildi"/"Hata" dönebiliyor (ör. aynı anda iki yükleme)
      if (result?.status !== "Success") {
        alert(result?.message || "Dosya işlenemedi.");
      }

      //Listeye kendimiz eklemek yerine backendten çekiyoruz; backend dosya adını değiştirebiliyor (".PDF" -> ".pdf")
      const savedName = file.name.trim().replace(/\.pdf$/i, '.pdf');
      let refreshedDocs: DocumentItem[] | null = null;
      try {
        refreshedDocs = await apiService.getExistingDocuments();
      } catch (error) {
        console.error("Yükleme sonrası döküman listesi yenilenemedi:", error);
      }

      setUploadProgress(100);
      setTimeout(() => {
        setIsUploading(false);
        setUploadProgress(0);
        if (refreshedDocs) {
          setDocuments(refreshedDocs);
          if (refreshedDocs.some((doc) => doc.name === savedName)) {
            setSelectedDoc(savedName);
          }
        }
      }, 400);

    } catch (error: any) {
      clearInterval(progressInterval);
      setIsUploading(false);
      setUploadProgress(0);
      alert(error.message || "Dosya yüklenirken bir hata oluştu.");
      console.error(error);
    }
  };

  //Mesaj gönderme
  const handleSend = async () => {
    //Cevap beklenirken ikinci istek gitmesin; yazılan soru kutuda kalır, cevap gelince gönderilebilir
    if (!input.trim() || isLoading) return;

    const userMessage: Message = { role: 'user', text: input };
    setMessages((prev) => [...prev, userMessage]);
    
    const userQuery = input;
    setInput('');

    setIsLoading(true);

    try {
      const result = await apiService.askQuestion(userQuery);
      console.log("LLM frontende yanıtını döndü.", result);

     
      const tokenData = result?.total_token_usage;
      const currentTotal = tokenData?.total_tokens || 0;

      
      
      const botMessage: Message = { 
        role: 'assistant', 
        text: result?.data || "Cevap üretilemedi.",
        resources: result?.resource || [], 
        total_token_usage: currentTotal 
      };
      setMessages((prev) => [...prev, botMessage]);

    } catch (error: any) {
      console.error("Query Error:", error);
      const errorMessage: Message = {
        role: 'assistant',
        text: error.message || "Üzgünüm, sorunuza yanıt üretilirken bir hata meydana geldi."
      };
      setMessages((prev) => [...prev, errorMessage]);
    }
    finally {
    // 2. İşlem başarılı da olsa hatalı da olsa loading'i kapat
    setIsLoading(false); 
  }
  };

  //Dökümanın adını aldık
  const handleSetSelectedDoc = (doc: string | null) => {
    if (!doc) {
      setSelectedDoc(null);
      return;
    }
    if (doc.startsWith('http')) {
      const parts = doc.split('/');
      setSelectedDoc(decodeURIComponent(parts[parts.length - 1]));
    } else {
      setSelectedDoc(doc);
    }
  };

  return (
    <main className="flex h-screen w-screen bg-slate-50 text-black font-sans overflow-hidden">
      
      {/* 1. BÖLÜM: SOL PANEL */}
      <LeftPanel
        isSidebarOpen={isSidebarOpen} 
        setIsSidebarOpen={setIsSidebarOpen} 
        selectedDoc={selectedDoc} 
        setSelectedDoc={handleSetSelectedDoc}
        searchTerm={searchTerm} 
        setSearchTerm={setSearchTerm} 
        filteredDocuments={filteredDocuments}
      />

      {/* 2. BÖLÜM: ORTA SOHBET VE INPUT ALANI */}
      <ChatArea 
        messages={messages} 
        isSidebarOpen={isSidebarOpen} 
        setIsSidebarOpen={setIsSidebarOpen} 
        input={input}
        setInput={setInput}
        onSend={handleSend}
        onFileUpload={handleFileUpload}
        isUploading={isUploading}
        uploadProgress={uploadProgress}
        setSelectedDoc={handleSetSelectedDoc} 
        isLoading={isLoading}
       
      />

      {/* 3. BÖLÜM: SAĞ PDF ÖNİZLEME ALANI */}
      <PdfViewer 
        selectedDoc={selectedDoc ? `http://localhost:8000/static-pdfs/${selectedDoc}` : null} 
        setSelectedDoc={handleSetSelectedDoc} 
      />

    </main>
  );
}