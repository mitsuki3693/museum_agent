# Conservation workspace UI demonstration

Scope requested: one demonstrable conservation-side interface. Route: `/conservation-demo`. The existing visitor experience and `/staff-demo` crowd-diversion example remain separate.

## Case and interactions

- A fictional exhibition area and display-case monitor C-03. Nine synthetic samples from 09:00 to 11:00; humidity and temperature charts can be switched. Two additional fictional monitor snapshots are shown in an expandable table.
- The last four humidity samples exceed an explicitly illustrative 65% RH reminder line. This is not a museum requirement or universal conservation threshold. The data cannot prove damage, causation or an optimal control setting.
- Three preset questions explain the reminder, insufficient evidence for damage, and missing conditions for equipment changes. Users can select them or type an exact preset question. Other queries receive an explicit out-of-scope response; no live model or retrieval is represented.
- Evidence details distinguish project-authored rule D01 v0.1 from a paraphrase of public CCI guidance G01, with scope, source and limitations. Opening a source enables acknowledgement. A non-empty editable opinion plus acknowledgement permits saving one simulated review record.
- Saving changes workflow status to “recorded, awaiting remeasurement.” Environmental values remain unchanged. Repeated submission is disabled. The record can be downloaded as a labelled Markdown demo artifact; reset/refresh clears local UI state.

## Evidence and implementation boundaries

The public guidance was checked 2026-09-30: [CCI Climate guidelines overview](https://www.canada.ca/en/conservation-institute/services/preventive-conservation/climate-guidelines/climate-guidelines-overview.html), specifically “Introduction to guidelines and specifications” and “Purpose and limitations of climate guidelines.” Only a short attributed Chinese paraphrase is included. The invented rule is separately labelled and does not acquire authority from this citation.

This is client-side synthetic UI state with no backend/API calls, internal files, real sensor data, model charges, permanent storage, device actions or staff authentication. Role labels are demonstrative. Real conservation deployment requires server-side identity/authorization and access-scoped retrieval; none is claimed by this page. Existing authenticated review endpoints are unchanged.

Source fixtures are in `web/src/lib/conservation-demo.ts`. The page and chart use React, native SVG and scoped CSS; no new dependencies. Validation uses production build/type checking and direct browser exercise of charts, preset/out-of-scope questions, citations, acknowledgement, saving, exporting and reset. It is not a staff trial or RAG evaluation.

## Verification — 2026-09-30

- Production build, lint and TypeScript checks passed.
- Browser checks confirmed humidity/temperature switching; all three preset answers; an out-of-scope query; public-source details; and disabled save before source review and acknowledgement.
- Saving an edited opinion produced a single record with source versions, kept readings unchanged and left the case awaiting remeasurement. Repeat submission was disabled. Reset cleared the record and re-disabled acknowledgement/save.
- The browser download-event observer timed out, but the Markdown file was actually saved to the local Downloads folder. Its contents were read back and confirmed to contain the edited opinion, synthetic-data notice, pending-remeasurement status and source limitations.
- Layout checks at 390 px and 1280 px found no horizontal overflow; the desktop layout was visually inspected. This is browser-size emulation, not a new physical iPhone test.
- Screenshot and downloaded test record remain local; no private museum assets are included in this change.
