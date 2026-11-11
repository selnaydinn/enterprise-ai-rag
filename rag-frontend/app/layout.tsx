import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

//latin-ext: ğ, ş, ı, İ gibi Türkçe harfler bu alt kümede; yalnızca "latin" ile bu harfler sistemin yedek fontuyla
//çiziliyor ve aynı kelimede iki farklı yazı tipi görünüyordu (notes.md #34)
const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin", "latin-ext"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin", "latin-ext"],
});

export const metadata: Metadata = {
  title: "Enterprise AI",
  description: "Kurumsal dokümanlar üzerinde soru-cevap",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  //lang="tr": ekran okuyucu metni Türkçe okur, tarayıcı çeviri önermez, yazım denetimi Türkçe çalışır
  return (
    <html
      lang="tr"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  );
}
