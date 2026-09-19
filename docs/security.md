# Security

What this project does, and — more importantly — what it deliberately leaves to
you. It is a reference implementation; treat the list under
[Before deploying](#before-deploying) as required work, not optional hardening.

---

## Secrets

- **Everything comes from the environment.** `src/config/settings.py` is the
  only module that reads `os.environ`, and no endpoint or credential has a
  hardcoded default. A test asserts this.
- **Nothing credential-shaped is committed.** `.env` and `*.pem` / `*.key` are
  gitignored; `.env.example` ships empty placeholders for every credential
  variable.
- **`./scripts/scan_secrets.sh`** checks for a tracked `.env`, tracked key
  material, secret-shaped strings (API-key formats, private keys, JWTs, DSNs
  with inline credentials), and values filled into `.env.example`. It runs in
  CI on every push.

For a deployment, use a secret manager — AWS Secrets Manager, Vault, GCP Secret
Manager, or your platform's sealed secrets — and inject at runtime. Do not bake
secrets into the image: the `.dockerignore` excludes `.env` precisely so an
accidental local file cannot be copied into a layer.

Rotate any credential that has ever been committed, anywhere. Deleting the file
does not remove it from history.

---

## Logging

Logs are the easiest place for a secret or a customer record to escape, because
they are widely readable and rarely reviewed.

- Embedding failures log the **exception type only** — never the endpoint, the
  credential, or the input text. Embedding inputs are document content and may
  contain anything in your corpus. `tests/test_embeddings.py` asserts that a
  transport failure leaks none of the three.
- Vector-store errors log the operation and the exception type, not the
  connection string.
- The API logs exceptions with a stack trace but returns a generic message to
  the client, so internal structure does not reach a caller.

If you add logging, log identifiers and types. Never log a prompt, a retrieved
chunk, or a model response at INFO.

---

## API authentication

Every endpoint except `/health` and `/v1/models` requires `X-API-Key`, compared
against `API_KEY` with `hmac.compare_digest` over SHA-256 digests. A missing
`API_KEY` is a 500, not an open door — the service refuses to serve
unauthenticated rather than failing open.

**This is a single shared secret.** It is enough to keep a service off the open
internet and no more. It gives you no per-user identity, no revocation, no
scopes, and no audit trail. For a deployment, put an API gateway or an
OIDC-aware proxy in front and treat this as a backstop.

---

## Transport and browser surface

- **CORS** defaults to `http://localhost:8502` — the local UI only. Widen
  deliberately via `CORS_ALLOW_ORIGINS`; `*` with `allow_credentials` is a
  mistake the default avoids.
- **Security headers** on every response: `X-Content-Type-Options: nosniff`,
  `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`.
- **`/docs` is disabled** when `ENVIRONMENT=production`.
- **TLS is not handled here.** Terminate it at your ingress or load balancer.

---

## Rendering model output

Model output is untrusted content. A retrieved document could contain markup,
and a model can be induced to reproduce it.

- Chainlit renders Markdown with `unsafe_allow_html = false`.
- The share page renders through `marked` and then **DOMPurify**, with the
  payload injected as JSON rather than interpolated into HTML.
- Share pages are served with `<meta name="robots" content="noindex">`.

If you add a surface that renders answers, sanitise it. Do not pass model
output to `innerHTML`, `dangerouslySetInnerHTML`, or an equivalent.

---

## Share links

A share link is a capability: a random UUID that grants read access to exactly
one answer. There is no listing endpoint and no enumeration path, and an
invalid or unknown token returns the same 404 as a deleted one.

But it is **unauthenticated by design** — anyone with the link can read the
answer. Do not enable sharing on a corpus whose answers should not be
forwardable, and note there is currently no expiry or revocation.

---

## Filesystem access

`POST /ingest/repository` reads from the server's filesystem, which makes it
the most dangerous endpoint in the project. Two controls:

1. It is **disabled** unless `REPO_INGEST_ROOT` names an allowed root.
2. Submitted paths are resolved and checked to fall inside that root, so
   `../` traversal is rejected.

`CodeRepositoryIngestion` additionally verifies that each file resolves inside
the repository root, so a symlink pointing outside the tree is skipped rather
than indexed.

Leave it disabled unless you need it.

---

## UI authentication

Off by default: without `OAUTH_GOOGLE_CLIENT_ID` the UI runs anonymously, which
is right locally and wrong anywhere else.

The shipped callback accepts **any** account the provider authenticates.
Deciding *which* accounts may sign in is deployment policy — add the check in
`chainlit_app.py`:

```python
@cl.oauth_callback
def oauth_callback(provider_id, token, raw_user_data, default_user):
    email = raw_user_data.get("email", "")
    if not email.endswith("@your-domain.example"):
        return None  # reject
    return default_user
```

Supply your own OAuth application. Never reuse someone else's client ID and
secret.

---

## Your corpus is your attack surface

Two properties of RAG that are easy to overlook:

**Anything indexed can be returned.** There is no per-user filtering on
retrieval — if a document is in a domain, any user who can query that domain
can extract its contents. Do not index anything the least-privileged user of
the system should not see. Partition by domain and put authorisation in front
of the API if you need finer control.

**Retrieved text reaches the model as context.** A document containing text
shaped like an instruction ("ignore previous instructions and…") is a prompt
injection with a persistent foothold. Treat the corpus as a trust boundary:
index sources you control, and review what you ingest from elsewhere.

Keep personal data out of the demo dataset entirely. The bundled corpus in
`data/` is synthetic and describes fictional services.

---

## Dependencies

`requirements.txt` uses lower bounds, so a fresh install picks up patches. For
a deployment, pin exact versions and generate a lockfile:

```bash
pip freeze > requirements.lock
```

Add `pip-audit` or Dependabot to catch advisories. CI currently runs lint,
tests and a secret scan; dependency scanning is not yet wired in.

---

## Before deploying

- [ ] Rotate every credential that was ever committed anywhere
- [ ] Move secrets into a secret manager; inject at runtime
- [ ] Put an authenticating gateway in front of the API
- [ ] Set `ENVIRONMENT=production` (disables `/docs`)
- [ ] Narrow `CORS_ALLOW_ORIGINS` to your real origins
- [ ] Terminate TLS at the ingress
- [ ] Restrict OAuth sign-in to accounts you intend to admit
- [ ] Leave `REPO_INGEST_ROOT` unset unless you need it
- [ ] Confirm the corpus contains nothing the least-privileged user may not see
- [ ] Pin dependency versions and enable advisory scanning
- [ ] Add rate limiting — there is none in this repository
- [ ] Run `./scripts/scan_secrets.sh` one more time

---

## Reporting a vulnerability

Open a private security advisory on the repository rather than a public issue.
