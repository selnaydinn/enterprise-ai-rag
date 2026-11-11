//Backend yanıt tipleri; schemas.py'deki modellerle birebir aynı (ikisi birlikte güncellenmeli)

//Token kullanım durumu (/usage/ ve /query/ yanıtındaki usage)
export interface UsageStatus {
  used: number;
  limit: number;
  resets_at: string;
  limited: boolean;
}

//Cevapta kullanılan doküman parçası
export interface Source {
  source_name: string;
  page: number;
  score: number;
  raw_text: string;
}

//success: cevap üretildi (yarım kaldıysa sonunda not var) · no_result: hiçbir parça barajı geçmedi (LLM çağrılmadı), LLM dokümanlarda cevap bulamadı veya boş cevap döndü
export interface QueryResponse {
  status: "success" | "no_result";
  message: string;
  answer: string;
  sources: Source[];
  token_usage: number;
  usage: UsageStatus;
}

//filename: diske kaydedilen ad (backend ".PDF" uzantısını ".pdf" yapabiliyor)
export interface UploadResponse {
  filename: string;
  total_pages: number;
  total_chunks: number;
  message: string;
}

export interface DocumentItem {
  name: string;
}

export interface DeleteResponse {
  name: string;
  deleted_chunks: number;
}

//Frontend'in hangi özellikleri göstereceği; ayar backend'de (.env ALLOW_DELETE)
export interface FeaturesResponse {
  allow_delete: boolean;
}

//GET /auth/me (main.py AuthStatus); oturum yoksa backend 401 döner, bu yanıt gelmez
export interface AuthStatus {
  authenticated: boolean;
}
