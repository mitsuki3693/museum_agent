# Public AI explanation service

The Pages frontend optionally calls `public-api`, a Cloudflare Worker with a SQLite-backed Durable Object for quota reservations. This is a small public-catalogue explanation service, not the full local RAG/vision stack. Its factual context is a server-owned, project-authored summary of three AIC works, with links to original catalogue pages. It does not publish private V&A source data.

## Request and trust boundary

- One selected work, a question (up to 600 characters), style, and at most three previous turns of that work are sent to DeepSeek through Cloudflare.
- The server validates fields, limits the body to 14 KB, selects evidence from its own catalogue, and caps model output at 600 tokens. It makes one call with a 25-second timeout and no automatic retries.
- Model output is labelled AI-generated, not independently fact-verified. Missing evidence should be acknowledged; a system instruction is not a guarantee against hallucination. Source links are server-owned.
- Photos stay in the browser; they are not sent to this interface. Staff/conservation pages remain simulations.
- The application does not log or persist dialogue. Cloudflare and DeepSeek still process requests under their service policies. Quota state stores a daily HMAC of the network address, not raw IPs; visitors on shared networks share a limit.

## Cost boundaries

- Three reservations per network address per UTC minute, 20 per UTC day; 100 across the site per UTC day; 1,000 for this release's lifetime.
- A single Durable Object commits quota reservations transactionally before calling the model. A restart or many concurrent Workers cannot reset the quota. Failure of quota storage blocks the provider call.
- Provider failures and cancelled requests still consume a reservation; retries are explicit. A cancellation in the browser cannot guarantee an already-started provider request incurs no cost.
- CORS is a browser boundary, not user authentication. A script can imitate Origin, but it cannot bypass the globally persisted request budget. This protects spending, not availability against quota exhaustion.
- These are request/token limits, not a billing-account balance or currency-denominated spending cap. Other uses of the same DeepSeek key are outside this budget. Keep billing notifications enabled at the provider.

## Deployment

From `public-api`, use `npm ci`, `npx wrangler login`, `npx wrangler deploy`. Only free-plan-compatible SQLite Durable Objects are used; do not enable a paid subscription automatically.

Store `DEEPSEEK_API_KEY` and a random `IP_SALT` using Wrangler secrets. Never put either in frontend variables, GitHub code, request bodies or logs. `AI_ENABLED=false` is the server kill switch. Quota storage must never be renamed or cleared during ordinary deployment.

After a live endpoint check succeeds, set the GitHub repository variable `MUSE_PUBLIC_AI_URL` to `https://muse-public-ai.muse-public-api.workers.dev/chat` and run the Pages workflow. The value is a public URL, not a credential. If the variable is absent, the frontend builds as a fully static demo.

Rollback: clear the repository variable and rebuild Pages, or disable `AI_ENABLED` and redeploy the Worker. Error states offer a clearly labelled preset explanation; they never silently count presets as AI results.

## Validation

Run `npx wrangler deploy --dry-run --outdir .wrangler/dry-run` and `npm test` in `public-api`. Tests cover validation, origin checks, disabled/missing-key states, quota failures, provider errors, truncation, daily/lifetime rollover, and 12 simultaneous reservations against an actual local SQLite Durable Object (only three accepted). Frontend tests cover request payloads, cancellation propagation, limits and wrong-work responses. These checks do not establish model answer quality.

The manual **Check public AI endpoint** workflow additionally sends exactly one public sample question through the deployed service; it consumes one quota reservation and one model call. A successful run establishes connectivity for that environment, not universal network availability.

### Release check — 2026-10-21

- 33 frontend checks and 9 API/runtime checks passed; the AI-enabled Pages build passed.
- Real deployed Worker → DeepSeek → source-linked response succeeded. On the public GitHub Pages site, a default explanation, a child-friendly rewrite, and the follow-up “你刚才说的水面通道是什么意思？” all returned labelled AI answers. The follow-up explained the phrase using the selected work's context.
- Initial newly-created Worker domain HTTPS failures later cleared without disabling certificate verification. CLI smoke checks use an explicit application User-Agent; a Python default-User-Agent request received 403 before a later application-identified request succeeded. Browser CORS preflight and response delivery were verified through the actual UI.
- This is connectivity and interaction acceptance on three public works, not an answer-accuracy, latency percentile, load or production-use study. Visual identification and the full local RAG stack remain outside this release.

References: [Cloudflare free SQLite Durable Objects](https://developers.cloudflare.com/changelog/post/2025-04-07-durable-objects-free-tier/), [Worker secrets](https://developers.cloudflare.com/workers/configuration/secrets/), [DeepSeek Chat API](https://api-docs.deepseek.com/api/create-chat-completion/).
