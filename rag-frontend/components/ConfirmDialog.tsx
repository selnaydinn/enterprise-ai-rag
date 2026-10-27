'use client';
import { useEffect, useRef } from "react";
import { Loader2 } from "lucide-react";

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  message: string;
  confirmLabel: string;
  //İşlem sürerken butonlar kapalı, pencere kapatılamaz
  busy: boolean;
  //İşlem başarısız olursa pencere açık kalır, hata burada gösterilir
  error: string | null;
  onConfirm: () => void;
  onCancel: () => void;
}

//Tarayıcının confirm() penceresi yerine uygulamanın kendi onay penceresi
//Yerleşik <dialog>: Esc ile kapanma, odağın pencerede kalması ve ekran okuyucu desteği hazır geliyor
export default function ConfirmDialog({ open, title, message, confirmLabel, busy, error, onConfirm, onCancel }: ConfirmDialogProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);

  //open prop'unu <dialog>'un kendi durumuyla eşliyoruz
  //Açılınca odak "Vazgeç"te: yanlışlıkla Enter'a basmak silme yapmasın
  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      dialog.showModal();
      cancelRef.current?.focus();
    } else if (!open && dialog.open) {
      dialog.close();
    }
  }, [open]);

  return (
    <dialog
      ref={dialogRef}
      aria-labelledby="confirm-dialog-title"
      //Esc: işlem sürmüyorsa vazgeç; tarayıcının kendi kapatmasını engelliyoruz ki durum tek yerden yönetilsin
      onCancel={(e) => {
        e.preventDefault();
        if (!busy) onCancel();
      }}
      className="fixed inset-0 m-auto h-fit w-full max-w-sm rounded-xl border border-slate-200 bg-white p-5 text-black shadow-lg backdrop:bg-slate-900/30"
    >
      <h2 id="confirm-dialog-title" className="text-sm font-bold">{title}</h2>
      <p className="mt-2 text-xs leading-relaxed text-slate-700">{message}</p>

      {error && (
        <p role="alert" className="mt-3 rounded-lg bg-red-50 border border-red-200 px-3 py-2 text-xs font-medium text-red-800">
          {error}
        </p>
      )}

      <div className="mt-5 flex justify-end gap-2">
        <button
          ref={cancelRef}
          onClick={onCancel}
          disabled={busy}
          className="rounded-lg border border-slate-200 bg-white px-3.5 py-2 text-xs font-semibold text-slate-700 hover:bg-slate-100 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        >
          Vazgeç
        </button>
        <button
          onClick={onConfirm}
          disabled={busy}
          className="flex items-center gap-1.5 rounded-lg bg-red-600 px-3.5 py-2 text-xs font-semibold text-white hover:bg-red-700 transition-colors disabled:opacity-60 disabled:cursor-not-allowed"
        >
          {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
          {confirmLabel}
        </button>
      </div>
    </dialog>
  );
}
