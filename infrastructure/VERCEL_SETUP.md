# Vercel setup and deployment

FamGuard deploys the Next.js app from [`frontend/`](../frontend/) to Vercel. The
repository's [CI workflow](../.github/workflows/ci.yml) deploys to production from
`main` after required checks pass. Configure the Vercel project and GitHub
`production` environment once before relying on that deployment path.

## 1. Create and configure the Vercel project

1. Import the repository in the [Vercel Dashboard](https://vercel.com/new).
2. Set **Root Directory** to `frontend`. Keep the framework as Next.js; the checked-in
   [`vercel.json`](../frontend/vercel.json) also declares this explicitly.
3. Add the environment variables below for the required environments. Use the
   development Clerk instance and staging API for Preview, and production credentials
   and API for Production. Do not commit actual values.

| Variable | Preview | Production |
|---|---|---|
| `NEXT_PUBLIC_API_BASE_URL` | Staging Render API base URL | Production Render API base URL |
| `NEXT_PUBLIC_AUTH_MODE` | `clerk` | `clerk` |
| `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` | Development Clerk publishable key | Production Clerk publishable key |
| `CLERK_SECRET_KEY` | Development Clerk secret key | Production Clerk secret key |
| `NEXT_PUBLIC_CLERK_SIGN_IN_URL` | `/sign-in` | `/sign-in` |

`NEXT_PUBLIC_*` values are included in the client build, so set them in Vercel before
building. `CLERK_SECRET_KEY` is server-only; never rename it with a `NEXT_PUBLIC_`
prefix. Configure the matching Clerk session-token claims and backend issuer/JWKS
settings as described in [Clerk setup](./CLERK_SETUP.md).

Add the deployed frontend origin to the API's `ALLOWED_ORIGINS` and the matching
Clerk instance's allowed origins. For a Preview deployment, use a stable staging
domain where possible; otherwise allow the exact preview hostname used for testing.
The API compares Clerk's `azp` claim against exact origins.

## 2. Link the project for CI

Link the local project from the frontend directory (this writes ignored
`frontend/.vercel/` metadata):

```powershell
Set-Location frontend
npx vercel link
```

Choose the team and project created above. In Vercel Project Settings, copy the
team/org ID and project ID. In the GitHub repository's **Settings → Environments →
production**, configure:

- Secret `VERCEL_TOKEN`: a Vercel token with access to this project.
- Variable `VERCEL_ORG_ID`: the team/org ID.
- Variable `VERCEL_PROJECT_ID`: the project ID.

The existing deploy job runs `vercel pull`, `vercel build --prod`, and
`vercel deploy --prebuilt --prod` from `frontend/` on a successful `main` push when
frontend files change. Do not put the token or project secrets in repository files.
Keep Vercel Git-triggered production deployments disabled if production must remain
gated by the repository's CI checks.

## 3. Validate Preview and Production

Create a Preview deployment from Vercel after Preview environment variables are set.
Confirm it loads the sign-in page, uses the staging API, and can sign in with a
development Clerk user. Run the staging end-to-end checks in the master tracker's
IC-4 checklist before promoting the configuration to production.

The first production deploy is triggered by a successful `main` push that changes
`frontend/` after the GitHub environment is configured. Confirm the Vercel deployment
is Ready, the production sign-in page loads, and authenticated API calls use the
production API and Clerk instance.

If configuration changes after a deployment, redeploy so the frontend is rebuilt
with the updated `NEXT_PUBLIC_*` values. The dashboard already shows a waking-up
message and a Retry action for temporary Render `502`/`503`/`504` responses.
