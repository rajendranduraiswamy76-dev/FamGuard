"use client";

import { useState } from "react";
import { useVault } from "@/lib/vault-context";
import { errorMessage } from "@/lib/hooks";
import { FinancialUpload } from "./FinancialUpload";
import { FinancialMaintenance } from "./FinancialMaintenance";

export function FinancialDataView() {
  const vault = useVault();
  const [version, setVersion] = useState(0);
  if (vault.error) return <div role="alert">
    <p>{errorMessage(vault.error)}</p>
    <button type="button" onClick={vault.reload} className="underline">Retry family</button>
  </div>;
  if (!vault.data) return <p role="status">Loading family...</p>;
  const { member, members, family } = vault.data;
  const available = member.role === "admin" ? members : [member];
  return <div className="space-y-6">
    <div className="page-heading"><div><h1>Income &amp; Expenses Data</h1>
      <p>Upload financial entries and maintain previously entered data.</p></div></div>
    <FinancialUpload members={available} defaultCurrency={family.currency}
      onSaved={() => setVersion((current) => current + 1)} />
    <FinancialMaintenance members={available} version={version} />
  </div>;
}
