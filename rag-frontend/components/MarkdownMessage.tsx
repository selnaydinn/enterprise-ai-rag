'use client';
import ReactMarkdown, { type Components } from "react-markdown";

//Gemini cevapları markdown yazıyor (**kalın**, - madde, 1. numaralı liste); düz metin olarak basılınca işaretler ham
//görünüyordu (notes.md #32). Yalnızca asistan mesajlarında kullanılıyor; kullanıcının sorusu düz metin kalıyor
//
//GÜVENLİK: react-markdown HTML'i çalıştırmaz. Cevapta "<script>" veya "<img onerror=...>" olsa bile etiket olarak
//değil metin olarak gösterilir (rehype-raw eklenmedikçe; eklenmemeli). "javascript:" gibi tehlikeli link adresleri de
//kütüphanenin varsayılan urlTransform'u ile temizleniyor. Cevap PDF içeriğinden üretildiği için bu önemli: bir PDF'e
//gömülü HTML/JS, Gemini'nin cevabı üzerinden sayfaya ulaşamaz. Bu yüzden ayrı bir sanitize kütüphanesi gerekmedi
//
//Stil: Tailwind sıfırlaması liste işaretlerini ve başlık boyutlarını kaldırıyor; öğeler burada balona uygun biçimde
//yeniden stilleniyor (yazı boyutu mesaj balonuyla aynı kalıyor)
const components: Components = {
  p: ({ children }) => <p className="mb-2 last:mb-0">{children}</p>,
  ul: ({ children }) => <ul className="mb-2 last:mb-0 list-disc pl-4 space-y-1">{children}</ul>,
  ol: ({ children }) => <ol className="mb-2 last:mb-0 list-decimal pl-4 space-y-1">{children}</ol>,
  li: ({ children }) => <li className="pl-0.5">{children}</li>,
  strong: ({ children }) => <strong className="font-semibold">{children}</strong>,
  //Başlıklar balonda büyük durmasın; yalnızca kalın ve biraz büyük
  h1: ({ children }) => <p className="mb-2 font-bold text-[13px]">{children}</p>,
  h2: ({ children }) => <p className="mb-2 font-bold text-[13px]">{children}</p>,
  h3: ({ children }) => <p className="mb-1.5 font-semibold">{children}</p>,
  blockquote: ({ children }) => <blockquote className="mb-2 border-l-2 border-slate-300 pl-3 text-slate-700">{children}</blockquote>,
  code: ({ children }) => <code className="rounded bg-slate-100 px-1 py-0.5 text-[11px]">{children}</code>,
  //Link yeni sekmede; noopener: açılan sayfa bu sekmeye erişemez
  a: ({ href, children }) => (
    <a href={href} target="_blank" rel="noopener noreferrer" className="text-indigo-600 underline">{children}</a>
  ),
  hr: () => <hr className="my-2 border-slate-200" />,
};

export default function MarkdownMessage({ text }: { text: string }) {
  return <ReactMarkdown components={components}>{text}</ReactMarkdown>;
}
