"use client";

import { HeroUIProvider, ToastProvider } from "@heroui/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { ThemeProvider } from "@/lib/theme";

export function Providers({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [queryClient] = useState(
    () => new QueryClient({ defaultOptions: { queries: { staleTime: 10_000, retry: 1 } } }),
  );
  return (
    <HeroUIProvider navigate={router.push}>
      <ToastProvider placement="bottom-right" />
      <ThemeProvider>
        <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
      </ThemeProvider>
    </HeroUIProvider>
  );
}
