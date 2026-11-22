# Local AI test setup

The optional Ollama provider keeps extraction, chat and embeddings on the host and
stores vectors in a local SQLite file. It is not a private deployment: authentication,
original files, database, queue and email still need appropriate local services and
access controls. Do not use real family data or expose this development setup publicly.

Install Ollama on the application host and pull `qwen2.5vl:3b` and
`nomic-embed-text`. Set `AI_PROVIDER=ollama`, `FAKE_EXTERNALS=0`, and
`LOCAL_VECTOR_PATH` to a new persistent SQLite file accessible to both API and worker.
Disable Ollama cloud features and use only locally installed models. The embedding
provider currently supports only `nomic-embed-text`.
The default Ollama URL is `http://127.0.0.1:11434`; only loopback URLs are accepted.
The API and worker must share the host network namespace with Ollama. A normal Docker
bridge container cannot access host loopback. This is not a deployment recipe.

The local vector index uses 768-dimensional nomic embeddings. It records the embedding
model and dimensions and refuses an existing unmarked or incompatible index. Use a new
index and re-ingest documents when changing embedding models; do not reuse OpenAI vectors.
The development local-file storage returns `file://` links and is not a secure,
browser-compatible download service.
The local path is deliberately refused in production until the remaining private services
and deployment protections are implemented. The existing OpenAI/Pinecone path is unchanged.

## Locked VM bootstrap

`infrastructure/private-bootstrap.yml` is an explicitly incomplete, synthetic-only
bootstrap. API, frontend, Postgres, Redis, mail catcher and host Ollama bind to loopback.
API authentication is unconfigured and must return 503, including for the frontend's
test token. No document workflows are usable until self-hosted authentication and
protected downloads are implemented. No VPN or public HTTPS endpoint is configured.

Stage the repository at `/opt/famguard`, then run `infrastructure/private-bootstrap.sh`
through a one-time VM startup script. It creates a persistent root-readable password
file, builds services, checks readiness and locked authentication, then runs
`infrastructure/ollama-smoke.py` using the deployed API interpreter. The test uses a
generated synthetic image and checks expiry extraction, 768-dimensional embeddings,
local vector tenant isolation, context resolution and streamed chat. Remove startup
metadata after completion; Docker restart policies preserve services across reboots.
Do not run this script on a shared host with services already using these loopback ports.

### Observed inference limitations

The CPU-only VM smoke tests with `qwen2.5vl:3b` transcribed the synthetic
`2030-12-31` expiry but returned a null `expiration_date` for both card fixtures.
The overall inference check therefore fails; successful JSON parsing is not evidence
of correct document metadata. Vision inference took several minutes on the
2-vCPU host. Document/query embeddings, local vector tenant isolation, member
resolution and streamed chat passed the revised smoke test, but do not establish
end-to-end document accuracy. Keep this bootstrap restricted to synthetic data
until expiry extraction is corrected and revalidated.
