import type { Metadata } from 'next';
import './globals.css';
export const metadata: Metadata = { title: 'InvestIQ | Research Workspace', description: 'Evidence-first financial research workspace.' };
export default function RootLayout({children}:{children:React.ReactNode}) { return <html lang="en"><body>{children}</body></html>; }
