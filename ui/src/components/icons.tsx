import type { ReactNode } from "react";

const Icon = ({ children, size = 18 }: { children: ReactNode; size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"
    strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    {children}
  </svg>
);

export const Plus = () => <Icon><path d="M12 5v14M5 12h14" /></Icon>;
export const ArrowUp = () => <Icon><path d="M12 19V5M5 12l7-7 7 7" /></Icon>;
export const Paperclip = () => <Icon><path d="M21 11.5l-8.6 8.6a5.5 5.5 0 01-7.8-7.8l8.6-8.6a3.7 3.7 0 115.2 5.2l-8.6 8.6a1.8 1.8 0 01-2.6-2.6l7.9-7.9" /></Icon>;
export const File = () => <Icon><path d="M14 3H7a2 2 0 00-2 2v14a2 2 0 002 2h10a2 2 0 002-2V8z" /><path d="M14 3v5h5" /></Icon>;
export const Close = () => <Icon><path d="M6 6l12 12M18 6L6 18" /></Icon>;
export const Menu = () => <Icon><path d="M4 7h16M4 12h16M4 17h16" /></Icon>;
export const Layers = () => <Icon><path d="M12 3l9 5-9 5-9-5z" /><path d="M3 13l9 5 9-5" /></Icon>;
