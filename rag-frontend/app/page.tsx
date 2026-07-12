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
    if (!e.target.files || !e.target.files[0]) return;
    console.log("PDF yükleme işlemi başladı...");
    const file = e.target.files[0];
    console.log("Seçilen Dosya:", file);

    if (!file.name.endsWith('.pdf')) {
      alert("Lütfen sadece PDF dosyası yükleyin.");
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

      setUploadProgress(100);
      setTimeout(() => {
        setIsUploading(false);
        setUploadProgress(0);
        setDocuments((prevDocs) => [...prevDocs, { name: file.name }]);
        setSelectedDoc(file.name); 
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
    if (!input.trim()) return;

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
    <main className="flex h-screen w-screen bg-slate-50 text-slate-800 font-sans overflow-hidden">
      
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