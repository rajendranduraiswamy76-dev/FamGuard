import { render, type RenderOptions } from "@testing-library/react";
import type { ReactElement, ReactNode } from "react";

import { StaticTokenProvider } from "@/lib/auth";
import { VaultProvider } from "@/lib/vault-context";

function Providers({ children }: { children: ReactNode }) {
  return (
    <StaticTokenProvider token="test-token">
      <VaultProvider>{children}</VaultProvider>
    </StaticTokenProvider>
  );
}

export function renderWithApp(ui: ReactElement, options?: RenderOptions) {
  return render(ui, { wrapper: Providers, ...options });
}
