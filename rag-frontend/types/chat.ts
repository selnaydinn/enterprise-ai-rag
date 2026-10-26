import type { Source } from "@/types/api";

//Sohbetteki bir mesaj (yalnızca frontend); page.tsx ve ChatArea.tsx ortak kullanıyor
export interface Message {
  role: 'user' | 'assistant';
  text: string;
  sources?: Source[];
}
