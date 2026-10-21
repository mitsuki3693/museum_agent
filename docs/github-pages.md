# MUSE public static demonstration

The GitHub Pages edition can operate as a backend-free walkthrough or connect to the optional [public AI explanation service](public-ai.md) via the repository variable `MUSE_PUBLIC_AI_URL`.
Without that variable it does not execute any model. With it, selected-work questions and style requests call DeepSeek through a quota-limited Cloudflare Worker. Neither mode executes the full local retrieval/vision/MongoDB/evaluation pipelines.
The normal local application remains the default `npm run build` / `npm run dev` mode.

## Included

- Three AIC public-domain images already present in `web/public/collection`.
- Project-authored Chinese brief, child-friendly and detailed explanations, with per-work official source links.
- Deterministic preset name lookup and selected-work author/date/material examples. Unsupported questions are explicitly out of scope.
- Local photo preview only; manual example selection is never represented as image recognition.
- An authored fictional floor-plan walkthrough and simulated staff diversion. This is not the private V&A floor-plan asset.
- Existing conservation UI fixtures, without internal documents, devices or real roles.

Images: AIC IDs 16568, 28560, 27992, marked `is_public_domain=true` in `data/sample-images.json`.
The corresponding images are available under CC0 through AIC Open Access.
See https://www.artic.edu/open-access/open-access-images and the individual artwork links.
Chinese scripts are project summaries, not official audio transcripts.

## Build and publish

From `web`: `npm ci` then `npm run build:pages`. The export is `web/out` with `/museum_agent` base path.
No `.env` file, model weights, source corpus or database is required. Only `web/out` is uploaded as the Pages artifact.
GitHub repository Settings > Pages > Build and deployment must select **GitHub Actions**.
`.github/workflows/pages.yml` builds on main-branch web changes or manual dispatch and deploys the artifact.
Expected project URL: https://mitsuki3693.github.io/museum_agent/

Changes to `NEXT_PUBLIC_STATIC_DEMO` and `NEXT_PUBLIC_BASE_PATH` are build-time configuration, not browser credentials.
The static build copies only allowlisted source and three images to `.pages-build` before compiling, so it cannot load local `.env` files or overwrite the running local `.next` cache.
Do not copy `data/private`, `eval/private`, `.runtime`, `.env`, or raw V&A transcripts into `web/public`.
Do not add API keys to any NEXT_PUBLIC variable.

## Verification scope

Automated checks cover preset matching, unsupported-question handling, static assets and absence of network operations in static interaction components.
Browser acceptance must separately verify mobile/desktop layout, under-prefix links, images, styles, local upload preview, map playback and conservation controls.
Static interactions and feedback are not model evaluations, user adoption or a live service deployment.

On 2026-10-21, the isolated static build and all 30 frontend tests passed locally. Browser checks at desktop and 390px mobile width verified the public-mode notice, three loaded images, brief/child explanations, local-only photo preview, staff evidence-to-diversion interaction, animated route progress, and conservation evidence-to-review-record interaction. No model/API calls were made for this acceptance pass. Browser speech availability remains device-dependent.
