import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Bank Statement Analysis System",
  description: "Financial intelligence platform for investigators",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className="h-full antialiased"
    >
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  );
}
