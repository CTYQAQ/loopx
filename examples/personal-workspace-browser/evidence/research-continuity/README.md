# Research worker readback: packaged first viewports

These screenshots use a disposable synthetic Goal and Agent. They contain no
live workspace, model session, private trace or user data.

- Before: packaged dashboard built from upstream
  `82f9c09bd61e26e458cecfd4cffeab2350359381`.
- After: the candidate source in this change, with the packaged bundle's
  source-file fingerprints verified against the checkout.
- Viewports: desktop `1512 × 982`; mobile `390 × 844`.
- Displayed locale: `zh-CN`. The same smoke also exercises `en` at both sizes.
- Browser: headless Chromium 151, with a CJK font installed.
- Preview boundary: locally served packaged dashboard; no hosted preview.

## Changed states

| State | Desktop | Mobile |
| --- | --- | --- |
| Before: no worker observation | [First viewport](research-worker-before-zh-CN-desktop.png) | [First viewport](research-worker-before-zh-CN-mobile.png) |
| Paused: the original replan obligation remains unresolved | [First viewport](research-worker-paused-zh-CN-desktop.png) | [First viewport](research-worker-paused-zh-CN-mobile.png) |
| Unknown: the original request requires reconciliation | [First viewport](research-worker-unknown-zh-CN-desktop.png) | [First viewport](research-worker-unknown-zh-CN-mobile.png) |

The observation sits in the existing Goal overview, before its acceptance and
work sections. It states the next useful action and keeps complete costs
unknown. Original operation details are expandable; the existing tasks link
and Goal Stop control remain available. It does not replace current admission
or claim that the Goal has finished.

## Reproduction and behavioral checks

Run the existing packaged browser entry point with a working Playwright browser:

```bash
LOOPX_PERSONAL_WORKSPACE_SCENARIO=research-worker-readback LOOPX_PERSONAL_WORKSPACE_PACKAGED=1 \
  node examples/personal-workspace-browser-smoke.mjs
```

The scenario uses `tests/control_plane/research_worker_readback_fixture.py` to
produce an actual worker observation through the File provider and real CLI,
then reads it through the Chat HTTP API and packaged page. Both states, locales
and viewport sizes are exercised. Checks cover horizontal overflow, unexpected
browser errors, original identity details, and task navigation. Missing,
corrupt and differently scoped associations hide stale operation identity.
Reads and navigation leave canonical Goal state and dispatch counts unchanged;
the existing Stop action actually stops the synthetic Goal without another
worker dispatch.

An absent optional Agent endpoint may return its typed session-preparation
error; this is handled separately from business writes. The fixture does not
install a model provider, invoke a paid model or claim to measure complete
provider costs.
