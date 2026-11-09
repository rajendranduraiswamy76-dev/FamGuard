# Clerk setup

This guide configures Clerk for FamGuard's Next.js frontend and FastAPI backend. Create
separate Clerk instances for development and production so local and deployed keys and
origins remain isolated. Never commit Clerk keys or session tokens.

## 1. Create the Clerk instance

1. Create a Clerk application in the [Clerk Dashboard](https://dashboard.clerk.com/).
   Use the development instance for local testing. Create or promote a production
   instance separately before deployment.
2. Enable the sign-in methods you intend to offer. Ensure each test account has a
   primary email address; FamGuard uses the email to match family invitations.
3. In the Clerk Dashboard, open **Sessions** and find **Customize session token**.
   Add the following custom claims, preserving the exact claim names and template
   expressions:

   ```json
   {
     "email": "{{user.primary_email_address}}",
     "name": "{{user.full_name}}"
   }
   ```

   Save the changes. The backend needs both claims: `email` for invite matching and
   `name` for family-member display names and alert emails. Sign out and back in (or
   refresh the session token) after changing the claims.

## 2. Configure the frontend

From the Clerk Dashboard's API keys page, copy the instance's publishable key and
secret key into `frontend/.env.local`:

```dotenv
NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=pk_test_...
CLERK_SECRET_KEY=sk_test_...
```

Use production keys in the deployed frontend environment, not the development keys.
Keep `NEXT_PUBLIC_AUTH_MODE=clerk` and the sign-in URL settings from
[`frontend/.env.example`](../frontend/.env.example). Do not expose the secret key
through a `NEXT_PUBLIC_` variable.

## 3. Configure the backend

Use the Clerk instance's Frontend API domain as the issuer, without a trailing slash.
The JWKS URL is that same domain followed by `/.well-known/jwks.json`. For example,
if the issuer is `https://example.clerk.accounts.dev`, the JWKS URL is
`https://example.clerk.accounts.dev/.well-known/jwks.json`.

Set the following backend environment variables for the matching instance:

```dotenv
CLERK_ISSUER=https://<frontend-api-domain>
CLERK_JWKS_URL=https://<frontend-api-domain>/.well-known/jwks.json
ALLOWED_ORIGINS=http://localhost:3000
```

For deployment, replace the local origin with the exact frontend origin, including
scheme and hostname (for example, `https://app.example.com`). Add additional trusted
origins as a comma-separated list only when needed. Do not use `*`. Clerk's `azp`
claim is checked against this list when present, so preview deployments need an
explicit matching origin if they are used for authenticated API requests.

Keep the issuer, JWKS URL, keys, and frontend origins from the same Clerk instance.
Store production values in the deployment provider's secret/environment settings,
not in source control.

## 4. Verify the configuration

Install the backend dependencies in the Python environment used to run the script.
From the repository root, set the backend values in that shell and run the checker:

```powershell
$env:CLERK_ISSUER = "https://<frontend-api-domain>"
$env:CLERK_JWKS_URL = "https://<frontend-api-domain>/.well-known/jwks.json"
$env:ALLOWED_ORIGINS = "http://localhost:3000"
python infrastructure\scripts\check_clerk.py
```

Sign in to the local frontend with a test user, then retrieve a short-lived token in
the browser's developer-tools console with:

```javascript
await window.Clerk.session.getToken()
```

Paste the returned token into the checker's hidden prompt. The token is not accepted
as a command-line argument and is never printed by the script. Use it only for this
check; do not save or share it. A successful check confirms the issuer, signature,
expiry, authorized origin (when present), and required `email` and `name` claims
using the same verifier as the API.

The check requires network access to Clerk's JWKS endpoint. If it fails, confirm the
issuer/JWKS pair, exact frontend origin, saved session-token claims, and that the
session token was issued after the claim change.

## 5. Deployment checklist

- [ ] Production instance and production frontend keys are configured.
- [ ] Production issuer and JWKS URL both refer to that instance.
- [ ] `ALLOWED_ORIGINS` lists the deployed frontend origin(s) exactly.
- [ ] Session-token claims `email` and `name` are saved and present in a verified token.
- [ ] Frontend and backend secrets are configured in their deployment environments;
      no real keys or tokens are in `.env.example`, logs, or commits.

See [Clerk's session-token documentation](https://clerk.com/docs/guides/sessions/customize-session-tokens)
for details on custom claims.
