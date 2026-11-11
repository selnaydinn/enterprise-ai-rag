'use client';
import { useState } from "react";
import Image from 'next/image';
import { LogIn } from "lucide-react";
import { AuthError, apiService } from "@/services/api";

interface LoginFormProps {
  //Parola doğrulanıp cookie kurulunca page.tsx uygulamayı gösterir
  onLoggedIn: () => void;
}

//Tek kullanıcılı giriş; parola backend'de .env APP_PASSWORD ile karşılaştırılıyor (core/auth.py)
//Parola yalnızca bu bileşenin state'inde ve istek gövdesinde; hiçbir yere kaydedilmiyor, loglanmıyor
export default function LoginForm({ onLoggedIn }: LoginFormProps) {
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleSubmit = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    if (!password || isSubmitting) return;

    setIsSubmitting(true);
    setError(null);

    try {
      await apiService.login(password);
      //Başarılı girişte bu bileşen ekrandan kalkıyor; state'i sıfırlamaya gerek yok
      onLoggedIn();
    } catch (err: unknown) {
      if (err instanceof AuthError) {
        //Yanlış parola: alan temizlenir, yeniden yazılabilir
        setPassword('');
        setError(err.message);
      } else if (err instanceof TypeError) {
        //fetch ağ hatasında TypeError fırlatır (backend kapalı, adres yanlış)
        setError("Sunucuya ulaşılamadı. Backend'in çalıştığından emin olun.");
      } else {
        //429 (çok fazla deneme) dahil; backend mesajı kalan süreyi içeriyor
        setError((err instanceof Error && err.message) || "Giriş yapılamadı. Lütfen tekrar deneyin.");
      }
      setIsSubmitting(false);
    }
  };

  return (
    <main className="flex h-screen w-screen items-center justify-center bg-slate-50 text-black font-sans">
      <form
        onSubmit={handleSubmit}
        className="w-full max-w-sm bg-white border border-slate-200 rounded-2xl shadow-sm p-8 flex flex-col gap-5"
      >
        <div className="flex items-center gap-3">
          <div className="relative w-12 h-12 shrink-0">
            <Image src="/logo.png" alt="Enterprise AI Logo" fill className="object-contain" priority />
          </div>
          <div className="flex flex-col">
            <h1 className="text-lg font-bold tracking-tight leading-none">Enterprise AI</h1>
            <span className="text-[11px] font-medium mt-1">Devam etmek için giriş yapın</span>
          </div>
        </div>

        <div className="flex flex-col gap-1.5">
          <label htmlFor="password" className="text-xs font-semibold">Parola</label>
          {/* autoComplete: tarayıcının parola yöneticisi parolayı önerebilsin */}
          <input
            id="password"
            type="password"
            autoComplete="current-password"
            autoFocus
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            disabled={isSubmitting}
            aria-invalid={error !== null}
            aria-describedby={error ? "login-error" : undefined}
            className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-100 disabled:bg-slate-50"
          />
        </div>

        {error && (
          <p id="login-error" role="alert" className="text-xs font-medium text-red-600">
            {error}
          </p>
        )}

        <button
          type="submit"
          disabled={!password || isSubmitting}
          className="flex items-center justify-center gap-2 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-indigo-700 disabled:cursor-not-allowed disabled:bg-slate-300"
        >
          <LogIn className="h-4 w-4" />
          {isSubmitting ? "Giriş yapılıyor..." : "Giriş yap"}
        </button>
      </form>
    </main>
  );
}
