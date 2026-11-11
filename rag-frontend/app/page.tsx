'use client';
import { useEffect, useState } from "react";
import LoginForm from "@/components/LoginForm";
import Workspace from "@/components/Workspace";
import { apiService, setUnauthorizedHandler } from "@/services/api";

//GİRİŞ KAPISI
//Oturum açık değilse uygulama (Workspace) hiç ekrana gelmez, dolayısıyla backend'e hiçbir veri isteği gitmez
//checking: açılışta /auth/me bekleniyor · anonymous: giriş ekranı · authenticated: uygulama
//unreachable: backend'e ulaşılamadı (kapalı, adres yanlış)
type AuthState = "checking" | "anonymous" | "authenticated" | "unreachable";

export default function Home() {
  const [authState, setAuthState] = useState<AuthState>("checking");
  //"Tekrar dene"de artırılır; oturum kontrolü yeniden çalışır
  const [checkCount, setCheckCount] = useState(0);

  //Açılışta oturum var mı (cookie geçerli mi); sayfa yenilense de oturum cookie sayesinde devam eder
  //setState effect içinde doğrudan değil, istek bitince callback'te çağrılıyor (react-hooks/set-state-in-effect)
  useEffect(() => {
    apiService.me()
      .then((authenticated) => setAuthState(authenticated ? "authenticated" : "anonymous"))
      .catch((error) => {
        console.error("Oturum kontrol edilirken bir hata oluştu:", error);
        setAuthState("unreachable");
      });
  }, [checkCount]);

  //Uygulama içindeki herhangi bir istek 401 alırsa (oturum süresi doldu, cookie silindi) giriş ekranına dönülür
  useEffect(() => {
    setUnauthorizedHandler(() => setAuthState("anonymous"));
    return () => setUnauthorizedHandler(null);
  }, []);

  const retry = () => {
    setAuthState("checking");
    setCheckCount((count) => count + 1);
  };

  if (authState === "authenticated") {
    return <Workspace onSignedOut={() => setAuthState("anonymous")} />;
  }

  if (authState === "anonymous") {
    return <LoginForm onLoggedIn={() => setAuthState("authenticated")} />;
  }

  return (
    <main className="flex h-screen w-screen items-center justify-center bg-slate-50 text-black font-sans">
      {authState === "checking" ? (
        <p className="text-sm font-medium text-slate-500">Yükleniyor...</p>
      ) : (
        <div className="flex flex-col items-center gap-3 text-center">
          <p role="alert" className="text-sm font-medium">
            Sunucuya ulaşılamadı. Backend&apos;in çalıştığından emin olun.
          </p>
          <button
            onClick={retry}
            className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-indigo-700"
          >
            Tekrar dene
          </button>
        </div>
      )}
    </main>
  );
}
