'use client';
import { useState, useEffect, useCallback, useRef } from "react";
import ChatArea from "@/components/ChatArea";
import PdfViewer from "@/components/PdfViewer";
import LeftPanel from "@/components/LeftPanel";
import ConfirmDialog from "@/components/ConfirmDialog";
import ToastList, { type ToastItem } from "@/components/Toast";
import { apiService, BACKEND_URL, TokenLimitError } from "@/services/api";
import type { DocumentItem, UsageStatus } from "@/types/api";
import type { Message } from "@/types/chat";

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

  //Token kullanımı mesajlardan ayrı tutuluyor; hata mesajından sonra sayaç sıfıra düşmesin
  const [usage, setUsage] = useState<UsageStatus | null>(null);

  //Silme butonu yalnızca backend'de ALLOW_DELETE açıksa görünür
  const [allowDelete, setAllowDelete] = useState(false);

  //Silinmesi onay bekleyen doküman; doluysa onay penceresi açık
  const [pendingDelete, setPendingDelete] = useState<string | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  //Tarayıcının alert() penceresi yerine sağ alt köşedeki bildirimler
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const toastId = useRef(0);

  const showToast = useCallback((message: string, type: ToastItem['type'] = 'error') => {
    toastId.current += 1;
    const id = toastId.current;
    setToasts((prev) => [...prev, { id, message, type }]);
  }, []);

  const dismissToast = useCallback((id: number) => {
    setToasts((prev) => prev.filter((toast) => toast.id !== id));
  }, []);

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

  //Silme özelliği açık mı; hata olursa buton gizli kalır
  useEffect(() => {
    apiService.getFeatures()
      .then((features) => setAllowDelete(features.allow_delete))
      .catch((error) => console.error("Özellik bilgisi çekilirken bir hata oluştu:", error));
  }, []);

  //Çöp kutusuna tıklanınca onay penceresi açılıyor; silme "Sil"e basılınca yapılıyor
  const handleDeleteDocument = (name: string) => {
    setDeleteError(null);
    setPendingDelete(name);
  };

  const cancelDelete = () => {
    setPendingDelete(null);
    setDeleteError(null);
  };

  //Onaydan sonra backend siler; hata olursa pencere açık kalır ve hata içinde gösterilir
  const confirmDelete = async () => {
    if (!pendingDelete) return;
    const name = pendingDelete;
    setIsDeleting(true);
    setDeleteError(null);

    try {
      await apiService.deleteDocument(name);
    } catch (error: unknown) {
      const message = error instanceof Error ? error.message : "";
      setDeleteError(message || "Doküman silinirken bir hata oluştu.");
      console.error(error);
      setIsDeleting(false);
      return;
    }

    setIsDeleting(false);
    setPendingDelete(null);
    showToast(`${name} silindi.`, 'success');
    //Silinen doküman önizlemede açıksa kapatıyoruz
    if (selectedDoc === name) {
      setSelectedDoc(null);
    }
    //Silme başarılı oldu; liste yenilenemezse yalnızca loga yazıyoruz
    try {
      setDocuments(await apiService.getExistingDocuments());
    } catch (error) {
      console.error("Silme sonrası döküman listesi yenilenemedi:", error);
    }
  };

  //Token kullanımını backendten çekiyoruz; kilit durumu sayfa yenilense de backendten geliyor
  const refreshUsage = useCallback(async () => {
    try {
      setUsage(await apiService.getUsage());
    } catch (error) {
      console.error("Token kullanım bilgisi çekilirken bir hata oluştu:", error);
    }
  }, []);

  //Sayfa açılışında sayacı yükledik
  //setState effect içinde doğrudan değil, istek bitince callback'te çağrılıyor (react-hooks/set-state-in-effect)
  useEffect(() => {
    apiService.getUsage()
      .then(setUsage)
      .catch((error) => console.error("Token kullanım bilgisi çekilirken bir hata oluştu:", error));
  }, []);

  //Sınır doluyken sıfırlanma anına kadar bekleyip kullanımı yeniden çekiyoruz, kilit böyle açılıyor
  //Bilgisayarın saati sunucudan farklıysa ve sınır hâlâ doluysa 30 sn sonra tekrar deniyoruz
  useEffect(() => {
    if (!usage?.limited) return;
    const untilReset = new Date(usage.resets_at).getTime() - Date.now();
    const wait = untilReset > 0 ? untilReset + 1000 : 30000;
    const timer = setTimeout(refreshUsage, wait);
    return () => clearTimeout(timer);
  }, [usage, refreshUsage]);

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
      showToast("Lütfen sadece PDF dosyası yükleyin.");
      return;
    }

    //Büyük dosyayı göndermeden önce reddediyoruz; asıl kontrol backendde
    if (file.size > MAX_UPLOAD_BYTES) {
      showToast(`Dosya boyutu ${MAX_UPLOAD_BYTES / (1024 * 1024)} MB sınırını aşıyor.`);
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

      //Hatalar artık HTTP koduyla geliyor (uploadPdf fırlatıyor); buraya yalnızca başarılı yükleme ulaşır
      //Listeye kendimiz eklemek yerine backendten çekiyoruz; kaydedilen ad backendten (".PDF" -> ".pdf" olabilir)
      const savedName = result.filename;
      showToast(`${savedName} yüklendi.`, 'success');
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

    } catch (error: unknown) {
      clearInterval(progressInterval);
      setIsUploading(false);
      setUploadProgress(0);
      const message = error instanceof Error ? error.message : "";
      showToast(message || "Dosya yüklenirken bir hata oluştu.");
      console.error(error);
    }
  };

  //Mesaj gönderme
  const handleSend = async () => {
    //Cevap beklenirken ikinci istek gitmesin; yazılan soru kutuda kalır, cevap gelince gönderilebilir
    //Token sınırı doluyken istek gönderilmiyor (asıl kontrol backendde)
    if (!input.trim() || isLoading || usage?.limited) return;

    const userMessage: Message = { role: 'user', text: input };
    setMessages((prev) => [...prev, userMessage]);
    
    const userQuery = input;
    setInput('');

    setIsLoading(true);

    try {
      const result = await apiService.askQuestion(userQuery);
      console.log("LLM frontende yanıtını döndü.", result);

      //Son istek sınırı aşarsa usage.limited true gelir ve input hemen kilitlenir
      setUsage(result.usage);

      const botMessage: Message = {
        role: 'assistant',
        text: result.answer || "Cevap üretilemedi.",
        sources: result.sources
      };
      setMessages((prev) => [...prev, botMessage]);

    } catch (error: unknown) {
      console.error("Query Error:", error);
      //Sınır başka bir sekmeden dolmuş olabilir; kilidi göstermek için kullanımı yeniden çekiyoruz
      if (error instanceof TokenLimitError) {
        refreshUsage();
      }
      const errorMessage: Message = {
        role: 'assistant',
        text: (error instanceof Error ? error.message : "") || "Üzgünüm, sorunuza yanıt üretilirken bir hata meydana geldi."
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
        onDeleteDocument={allowDelete ? handleDeleteDocument : undefined}
        onSignOut={() => showToast("Çıkış özelliği henüz yok (kimlik doğrulama eklenmedi).")}
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
        usage={usage}
      />

      {/* 3. BÖLÜM: SAĞ PDF ÖNİZLEME ALANI */}
      <PdfViewer 
        selectedDoc={selectedDoc ? `${BACKEND_URL}/static-pdfs/${encodeURIComponent(selectedDoc)}` : null} 
        setSelectedDoc={handleSetSelectedDoc}
      />

      {/* SİLME ONAYI VE BİLDİRİMLER (tarayıcının confirm/alert pencereleri yerine) */}
      <ConfirmDialog
        open={pendingDelete !== null}
        title="Doküman silinsin mi?"
        message={`${pendingDelete ?? ''} ve DB'deki tüm parçaları silinecek. Bu işlem geri alınamaz.`}
        confirmLabel="Sil"
        busy={isDeleting}
        error={deleteError}
        onConfirm={confirmDelete}
        onCancel={cancelDelete}
      />
      <ToastList toasts={toasts} onDismiss={dismissToast} />

    </main>
  );
}