'use client';
import { useEffect } from "react";
import { logError } from "@/services/api";

//HATA SAYFASI (notes2.md Faz 8)
//Sayfa çizilirken (render) bir bileşen hata fırlatırsa beyaz ekran ve İngilizce "Application error" yerine bu ekran gelir
//İsteklerin hataları burada değil, catch bloklarında yakalanıyor (toast / sohbet mesajı, services/api.ts)
//Next.js'in kuralları: Client Component olmalı; bu sürümde prop'lar error (tipi unknown) ve retry (reset değil)
//Kök layout.tsx'i kapsamaz (onun için global-error.tsx gerekir; layout yalnızca font ve <html>, bilerek eklenmedi)
//Bileşenin adı "Error" değil: JavaScript'in kendi Error'ını gölgeler
export default function ErrorPage({ error, retry }: { error: unknown; retry: () => void }) {
  //Koddaki gerçek bir hata: konsola kırmızı yazılır (logError, ApiError olmadığı için console.error)
  useEffect(() => {
    logError("Sayfa çizilirken beklenmeyen bir hata oluştu", error);
  }, [error]);

  //"Tekrar dene": sayfa baştan çizilir, oturum yeniden kontrol edilir; sohbet geçmişi (yalnızca bellekte) kaybolur
  //Görünüm page.tsx'teki "Sunucuya ulaşılamadı" ekranıyla aynı
  return (
    <main className="flex h-screen w-screen items-center justify-center bg-slate-50 text-black font-sans">
      <div className="flex flex-col items-center gap-3 text-center">
        <p role="alert" className="text-sm font-medium">
          Bir sorun oluştu. Lütfen tekrar deneyin.
        </p>
        <button
          onClick={() => retry()}
          className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-indigo-700"
        >
          Tekrar dene
        </button>
      </div>
    </main>
  );
}
