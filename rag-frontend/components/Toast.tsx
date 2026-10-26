'use client';
import { useEffect } from "react";
import { X, CircleAlert, CircleCheck } from "lucide-react";

export interface ToastItem {
  id: number;
  message: string;
  type: 'error' | 'success';
}

//Bildirimler bu süre sonra kendiliğinden kapanır
const TOAST_DURATION_MS = 4000;

interface ToastListProps {
  toasts: ToastItem[];
  onDismiss: (id: number) => void;
}

//Sağ alt köşedeki bildirimler; tarayıcının alert() penceresi yerine kullanılıyor
//aria-live: ekran okuyucular yeni bildirimi okusun
export default function ToastList({ toasts, onDismiss }: ToastListProps) {
  return (
    <div aria-live="polite" className="fixed bottom-6 right-6 z-50 flex flex-col gap-2 w-80 max-w-[calc(100vw-3rem)]">
      {toasts.map((toast) => (
        <ToastCard key={toast.id} toast={toast} onDismiss={onDismiss} />
      ))}
    </div>
  );
}

function ToastCard({ toast, onDismiss }: { toast: ToastItem; onDismiss: (id: number) => void }) {
  //Süre dolunca kapatıyoruz; setState zamanlayıcının callback'inde çağrılıyor
  useEffect(() => {
    const timer = setTimeout(() => onDismiss(toast.id), TOAST_DURATION_MS);
    return () => clearTimeout(timer);
  }, [toast.id, onDismiss]);

  const isError = toast.type === 'error';
  const Icon = isError ? CircleAlert : CircleCheck;

  return (
    <div
      role={isError ? "alert" : "status"}
      className={`flex items-start gap-2.5 rounded-xl border px-3.5 py-3 text-xs font-medium shadow-sm animate-in fade-in slide-in-from-bottom-1 duration-200 ${
        isError
          ? 'bg-red-50 border-red-200 text-red-800'
          : 'bg-emerald-50 border-emerald-200 text-emerald-800'
      }`}
    >
      <Icon className="h-4 w-4 shrink-0 mt-px" />
      <span className="flex-1 leading-relaxed">{toast.message}</span>
      <button
        onClick={() => onDismiss(toast.id)}
        className="p-0.5 rounded opacity-60 hover:opacity-100 transition-opacity shrink-0"
        aria-label="Bildirimi kapat"
      >
        <X className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}
