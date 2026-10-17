import './globals.css';
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body className="bg-[#070f1a] text-slate-100 font-sans antialiased">{children}</body>
    </html>
  );
}
