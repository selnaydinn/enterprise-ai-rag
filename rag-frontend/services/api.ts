import type { AuthStatus, DeleteResponse, DocumentItem, FeaturesResponse, QueryResponse, UploadResponse, UsageStatus } from "@/types/api";

//Backend adresi .env.local'daki NEXT_PUBLIC_API_URL'den gelir (build sırasında koda gömülür)
//Frontend localhost:3000'de açılıyorsa backend de "localhost" ile yazılmalı, "127.0.0.1" değil: tarayıcı ikisini farklı
//site sayar ve oturum cookie'sini (SameSite=Lax) göndermez; giriş başarılı görünür ama sonraki istekler 401 alır
export const BACKEND_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

//Backend'in döndüğü hata: message ekranda gösterilen Türkçe metin, status HTTP kodu (409, 500...)
//Kod, hatanın normal bir durum mu (409 zaten var) yoksa gerçek bir arıza mı (500) olduğunu ayırmak için (logError)
//requestId: backend'in bu isteğe verdiği X-Request-ID; 500'de konsola yazılır, backend logunda bu kodla aranınca
//hatanın ayrıntısı (traceback) bulunur. Kullanıcıya gösterilmiyor (notes2.md Faz 8; internete açılınca yeniden bakılacak)
export class ApiError extends Error {
  readonly status: number;
  readonly requestId: string | null;
  constructor(message: string, response: Response) {
    super(message);
    this.name = new.target.name;
    this.status = response.status;
    this.requestId = response.headers.get("X-Request-ID");
  }
}

//Token sınırı dolunca (429) fırlatılır; sayfa bunu görünce kullanımı yeniden çekip input'u kilitler
export class TokenLimitError extends ApiError {}

//Oturum yok, süresi dolmuş veya parola hatalı (401)
export class AuthError extends ApiError {}

//Backend'e hiç ulaşılamadı (kapalı, çökmüş, bağlantı koptu); fetch'in İngilizce "Failed to fetch" hatası yerine
//Türkçe mesaj. Tarayıcı CORS reddini de aynı şekilde bildirdiği için o da buraya düşer. Asıl hata cause'da
export class NetworkError extends Error {
  constructor(cause: unknown) {
    super("Sunucuya ulaşılamadı, lütfen tekrar deneyin.", { cause });
    this.name = "NetworkError";
  }
}

//İstek süresi doldu: tarayıcı beklemeyi bıraktı. Backend işi sürdürüyor olabilir (soru Gemini'ye gittiyse token harcanır)
export class RequestTimeoutError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "RequestTimeoutError";
  }
}

//Bekleme süreleri (notes2.md Faz 8). Backend'in 504'ü yalnızca Gemini aşamasını kapsıyor (20 sn × 2 deneme);
//bu süreler kuyrukta bekleme, takılan arama/rerank veya donmuş backend için son güvenlik
type Timeout = { ms: number; message: string } | null;

const DEFAULT_TIMEOUT: Timeout = { ms: 15_000, message: "İstek zaman aşımına uğradı, lütfen tekrar deneyin." };
//Gemini ~40 sn + yük altında arama ve rerank ~20 sn (yük testi, 4 kişi) üstüne pay
const QUERY_TIMEOUT: Timeout = { ms: 90_000, message: "Yanıt çok uzun sürdü. Lütfen biraz sonra tekrar deneyin." };
//Yüklemede süre yok: büyük PDF'ler (sayfa başına ~9 sn, 20 MB'ta 200+ sayfa) dakikalarca sürebilir; süre dolsa
//backend yüklemeyi yine bitirir ve kullanıcı yanlışlıkla "başarısız" görür. Backend çökerse bağlantı kopar (NetworkError)
const NO_TIMEOUT: Timeout = null;

//Yakalanan hataları konsola yazar. Yalnızca beklenmeyen hatalar console.error: dev modda Next.js kırmızı hata
//penceresini açar, gerçek bir arıza olduğu anlaşılsın. Backend'in 500'ü ve bilinmeyen her şey (koddaki bir hata)
//bu gruptadır. Diğer HTTP kodları (400, 401, 409, 413, 422, 429, 503, 504...) ve ağ hatası kullanıcı kaynaklı veya
//geçici, beklenen durumlar: console.warn, pencere açılmaz. Kullanıcıya gösterilen mesaj bundan etkilenmez
export function logError(context: string, error: unknown) {
  if (error instanceof ApiError && error.status !== 500) {
    console.warn(`${context} (HTTP ${error.status}):`, error.message);
  } else if (error instanceof NetworkError) {
    console.warn(`${context} (ağ hatası):`, error.cause);
  } else if (error instanceof RequestTimeoutError) {
    console.warn(`${context} (zaman aşımı):`, error.message);
  } else if (error instanceof ApiError) {
    //500: backend logunda aramak için istek kodu
    console.error(`${context} (HTTP ${error.status}, istek kodu: ${error.requestId ?? "yok"}):`, error);
  } else {
    console.error(`${context}:`, error);
  }
}

//Backend'in mesajı varsa o, yoksa fallback; HTTP kodu ve istek kodu her durumda taşınır
async function toApiError(response: Response, fallback: string): Promise<ApiError> {
  return new ApiError((await readDetail(response)) || fallback, response);
}

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
//fetch yalnızca cevap hiç gelmezse hata fırlatır (4xx/5xx cevaplar hata değil); bütün istekler için tek yerde NetworkError
//timeout: süre dolunca tarayıcı isteği bırakır (AbortSignal.timeout "TimeoutError" fırlatır) ve RequestTimeoutError
async function request(path: string, init: RequestInit = {}, redirectOnUnauthorized = true,
                       timeout: Timeout = DEFAULT_TIMEOUT): Promise<Response> {
  let response: Response;
  try {
    response = await fetch(`${BACKEND_URL}${path}`, {
      ...init,
      credentials: "include",
      signal: timeout ? AbortSignal.timeout(timeout.ms) : undefined,
    });
  } catch (error) {
    if (timeout && error instanceof DOMException && error.name === "TimeoutError") {
      throw new RequestTimeoutError(timeout.message);
    }
    throw new NetworkError(error);
  }
  if (response.status === 401 && redirectOnUnauthorized) {
    unauthorizedHandler?.();
    throw new AuthError((await readDetail(response)) || "Oturum açmanız gerekiyor.", response);
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
      throw new ApiError(`Sunucu hatası: ${response.status}`, response);
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
      throw new AuthError(detail || "Parola hatalı.", response);
    }
    throw new ApiError(detail || `Sunucu hatası: ${response.status}`, response);
  },

  //Backend cookie'yi siler
  async logout(): Promise<void> {
    const response = await request("/auth/logout", { method: "POST" }, false);
    if (!response.ok) {
      throw new ApiError(`Sunucu hatası: ${response.status}`, response);
    }
  },

  //Var olan dökümanları backendten çekiyoruz
  async getExistingDocuments(): Promise<DocumentItem[]> {
    const response = await request("/documents/");

    if (!response.ok) {
      throw new ApiError("Mevcut dokümanlar getirilirken backend hatası oluştu.", response);
    }
    return response.json();
  },

  //Token sayacı ve kilit durumu için kullanımı backendten çekiyoruz
  async getUsage(): Promise<UsageStatus> {
    const response = await request("/usage/");

    if (!response.ok) {
      throw new ApiError("Token kullanım bilgisi alınırken backend hatası oluştu.", response);
    }
    return response.json();
  },

  //Silme butonu gösterilsin mi (backend .env ALLOW_DELETE)
  async getFeatures(): Promise<FeaturesResponse> {
    const response = await request("/features/");

    if (!response.ok) {
      throw new ApiError("Özellik bilgisi alınırken backend hatası oluştu.", response);
    }
    return response.json();
  },

  //Dokümanı ve DB'deki parçalarını siliyoruz
  async deleteDocument(name: string): Promise<DeleteResponse> {
    const response = await request(`/documents/${encodeURIComponent(name)}`, {
      method: "DELETE",
    });

    if (!response.ok) {
      throw await toApiError(response, `Sunucu hatası: ${response.status}`);
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
    }, true, NO_TIMEOUT);

    if (!response.ok) {
      throw await toApiError(response, `Sunucu hatası: ${response.status}`);
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
    }, true, QUERY_TIMEOUT);

    //422'de detail metin değil liste dönüyor (Pydantic doğrulaması); ekranda [object Object] görünmesin
    if (response.status === 422) {
      throw new ApiError("Sorunuz geçerli şartları karşılamamaktadır.", response);
    }

    //Backend mesajı sıfırlanma saatini içeriyor
    if (response.status === 429) {
      throw new TokenLimitError((await readDetail(response)) || "Token kullanım sınırına ulaşıldı.", response);
    }

    if (!response.ok) {
      throw await toApiError(response, "Sorgu işlenirken backend hatası oluştu.");
    }

    return await response.json();
  }
};
