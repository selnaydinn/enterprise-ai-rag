//Backend adresi .env.local'daki NEXT_PUBLIC_API_URL'den gelir (build sırasında koda gömülür)
export const BACKEND_URL = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";

//Backenddeki token kullanım durumu (services/usage.py get_status)
export interface Usage {
  used: number;
  limit: number;
  resets_at: string;
  limited: boolean;
}

//Token sınırı dolunca (429) fırlatılır; sayfa bunu görünce kullanımı yeniden çekip input'u kilitler
export class TokenLimitError extends Error {}

export const apiService = {

  //Var olan dökümanları backendten çekiyoruz
  async getExistingDocuments(): Promise<Array<{ name: string }>> {
    const response = await fetch(`${BACKEND_URL}/documents/`, {
      method: "GET",
      headers: {
        "Content-Type": "application/json",
      },
    });

    if (!response.ok) {
      throw new Error("Mevcut dökümanlar getirilirken backend hatası oluştu.");
    }
    return response.json();
  },

  //Token sayacı ve kilit durumu için kullanımı backendten çekiyoruz
  async getUsage(): Promise<Usage> {
    const response = await fetch(`${BACKEND_URL}/usage/`);

    if (!response.ok) {
      throw new Error("Token kullanım bilgisi alınırken backend hatası oluştu.");
    }
    return response.json();
  },

  //PDF yüklendiği zaman backende istek atıyoruz
  uploadPdf: async (file: File) => {
    const formData = new FormData();
    
    formData.append("file", file);

    const response = await fetch(`${BACKEND_URL}/upload-pdf/`, {
      method: "POST",
      body: formData, 
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(errorData.detail || `Sunucu hatası: ${response.status}`);
    }

    return await response.json();
  },

  //Kullanıcı soru sorduğunda backende istek atıyoruz
  askQuestion: async (question: string) => {
    const response = await fetch(`${BACKEND_URL}/query/`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ question }), 
    });

    //422'de detail metin değil liste dönüyor (Pydantic doğrulaması); ekranda [object Object] görünmesin
    if (response.status === 422) {
      throw new Error("Sorunuz geçerli şartları karşılamamaktadır.");
    }

    //Backend mesajı sıfırlanma saatini içeriyor
    if (response.status === 429) {
      const errorData = await response.json().catch(() => ({}));
      const detail = typeof errorData.detail === "string" ? errorData.detail : null;
      throw new TokenLimitError(detail || "Token kullanım sınırına ulaşıldı.");
    }

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      const detail = typeof errorData.detail === "string" ? errorData.detail : null;
      throw new Error(detail || "Sorgu işlenirken backend hatası oluştu.");
    }

    return await response.json();
  }







};