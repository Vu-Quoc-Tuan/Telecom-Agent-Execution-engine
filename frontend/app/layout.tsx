import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Telecom Agent",
  description: "Operational console for telecom agent execution, approvals, and skill review.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="vi" className="h-full antialiased">
      <body className="min-h-full bg-main-background text-primary-text">{children}</body>
    </html>
  );
}
