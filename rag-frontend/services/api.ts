import type { AuthStatus, DeleteResponse, DocumentItem, FeaturesResponse, QueryResponse, UploadResponse, UsageStatus } from "@/types/api";

//Backend adresi .env.local'daki NEXT_PUBLIC_API_URL'den gelir (build sırasında koda gömülür)
//Frontend localhost:3000'de açılıyorsa backend de "localhost" ile yazılmalı, "127.0.0.1" değil: tarayıcı ikisini farklı
//site sayar ve oturum cookie'sini (SameSite=Lax) göndermez; giriş başarılı görünür ama sonraki istekler 401 alır
export const BACKEND_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

//Token sınırı dolunca (429) fırlatılır; sayfa bunu görünce kullanımı yeniden çekip input'u kilitler
export class TokenLimitError extends Error {}

//Oturum yok, süresi dolmuş veya parola hatalı (401)
export class AuthError extends Error {}

//Uygulama içindeki bir istek 401 alırsa (oturum süresi doldu, cookie silindi) page.tsx giriş ekranına döner
//page.tsx açılışta kaydeder; tek bir dinleyici yeterli
let unauthorizedHandler: (() => void) | null = null;

export function setUnauthorizedHandler(handler: (() => void) | null) {
  unauthorizedHandler = handler;
}

//Hata yanıtındaki detail metni; bizim hatalarımızda metin, Pydantic 422'sinde liste (o zaman null; ekranda [object Object] görünmesin)
async function readDetail(response: Response): Promise<string | null> {
  const errorData = await response.json().catch(() => ({}));
  return typeof errorData.detail === "string" ? errorData.detail : null;
}

//Tüm istekler buradan geçer
//credentials: "include": oturum cookie'si farklı porttaki backend'e de gönderilir (backend'de CORS allow_credentials açık)
//401'de giriş ekranına dönülür; login ve me kendi 401'lerini kendileri yorumladığı için bunu kapatıyor
async function request(path: string, init: RequestInit = {}, redirectOnUnauthorized = true): Promise<Response> {
  const response = await fetch(`${BACKEND_URL}${path}`, { ...init, credentials: "include" });
  if (response.status === 401 && redirectOnUnauthorized) {
    unauthorizedHandler?.();
    throw new AuthError((await readDetail(response)) || "Oturum açmanız gerekiyor.");
  }
  return response;
}

export const apiService = {

  //Oturum açık mı; açılışta giriş ekranını gösterip göstermeyeceğimize buradan karar veriyoruz
  //401 normal bir durum (giriş yapılmamış): false. Diğer hatalar fırlatılır (sunucuya ulaşılamadı)
  async me(): Promise<boolean> {
    const response = await request("/auth/me", {}, false);
    if (response.status === 401) return false;
    if (!response.ok) {
      throw new Error(`Sunucu hatası: ${response.status}`);
    }
    const status: AuthStatus = await response.json();
    return status.authenticated;
  },

  //Parola doğruysa backend oturum cookie'sini kurar (tarayıcı saklar, JS göremez)
  //401: parola hatalı · 429: çok fazla hatalı deneme, backend mesajı kalan süreyi içeriyor
  async login(password: string): Promise<void> {
    const response = await request("/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password }),
    }, false);

    if (response.ok) return;
    const detail = await readDetail(response);
    if (response.status === 401) {
      throw new AuthError(detail || "Parola hatalı.");
    }
    throw new Error(detail || `Sunucu hatası: ${response.status}`);
  },

  //Backend cookie'yi siler
  async logout(): Promise<void> {
    const response = await request("/auth/logout", { method: "POST" }, false);
    if (!response.ok) {
      throw new Error(`Sunucu hatası: ${response.status}`);
    }
  },

  //Var olan dökümanları backendten çekiyoruz
  async getExistingDocuments(): Promise<DocumentItem[]> {
    const response = await request("/documents/");

    if (!response.ok) {
      throw new Error("Mevcut dokümanlar getirilirken backend hatası oluştu.");
    }
    return response.json();
  },

  //Token sayacı ve kilit durumu için kullanımı backendten çekiyoruz
  async getUsage(): Promise<UsageStatus> {
    const response = await request("/usage/");

    if (!response.ok) {
      throw new Error("Token kullanım bilgisi alınırken backend hatası oluştu.");
    }
    return response.json();
  },

  //Silme butonu gösterilsin mi (backend .env ALLOW_DELETE)
  async getFeatures(): Promise<FeaturesResponse> {
    const response = await request("/features/");

    if (!response.ok) {
      throw new Error("Özellik bilgisi alınırken backend hatası oluştu.");
    }
    return response.json();
  },

  //Dokümanı ve DB'deki parçalarını siliyoruz
  async deleteDocument(name: string): Promise<DeleteResponse> {
    const response = await request(`/documents/${encodeURIComponent(name)}`, {
      method: "DELETE",
    });

    if (!response.ok) {
      throw new Error((await readDetail(response)) || `Sunucu hatası: ${response.status}`);
    }
    return response.json();
  },

  //PDF yüklendiği zaman backende istek atıyoruz
  uploadPdf: async (file: File): Promise<UploadResponse> => {
    const formData = new FormData();

    formData.append("file", file);

    const response = await request("/upload-pdf/", {
      method: "POST",
      body: formData,
    });

    if (!response.ok) {
      throw new Error((await readDetail(response)) || `Sunucu hatası: ${response.status}`);
    }

    return await response.json();
  },

  //Kullanıcı soru sorduğunda backende istek atıyoruz
  askQuestion: async (question: string): Promise<QueryResponse> => {
    const response = await request("/query/", {
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
      throw new TokenLimitError((await readDetail(response)) || "Token kullanım sınırına ulaşıldı.");
    }

    if (!response.ok) {
      throw new Error((await readDetail(response)) || "Sorgu işlenirken backend hatası oluştu.");
    }

    return await response.json();
  }
};
