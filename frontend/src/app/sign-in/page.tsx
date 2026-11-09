import { SignIn } from "@clerk/nextjs";

import { isTestAuth } from "@/lib/env";

export default function SignInPage() {
  if (isTestAuth) {
    return <p className="text-sm text-slate-500">Sign-in is disabled in test mode.</p>;
  }
  return (
    <div className="flex justify-center py-10">
      <SignIn routing="hash" />
    </div>
  );
}
