# Working on Gulliver's Hoard

Printable 3D map models (package `gulliver_hoard`, service `gulliver-hoard`, app id `gulliver`, port 5206). Read [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) first.

## Commands

```bash
python -m pytest                             # the whole suite, against the invented town (no network)
node --test client/copilot-context.test.mjs  # the Faustus panel context
npx vite build                               # rebuild the UI into gulliver_hoard/static
python -m gulliver_hoard                     # http://127.0.0.1:5206 (GULLIVER_DATA_DIR=<folder> for a scratch copy)
GULLIVER_FAKE=1 python -m gulliver_hoard     # the invented town, no network
```

## Rules of the code

- The UI, the REST agent route and MCP call the same handlers in `agent_tools.py`. A new tool needs a pydantic argument model, a first description line of at most 110 characters (English, then Spanish keywords), synonyms, annotations and a test (`test_every_tool_is_tested` fails otherwise).
- Every solid is a manifold3d `Manifold`. Never let two solids meet on a coincident face in a union or a difference: grow or shrink one of them by a few hundredths of a millimetre (see the frame, buildings, colour layers and banks). Every part, the one-piece twin and every tile must pass the open-edge check of `outputs._check` (tests do this).
- New layers or parameters go in `ModelParams` (validated, with a description) and in the UI form, the i18n table (castellano de España first, English second) and the README in both languages.
- Network goes through `sources/http.Fetcher` with its cache. The tests never touch the network: use `FakeSources` or an `httpx.MockTransport`.
- `hoard_link/` is vendored and byte-identical to upstream HoardLink: never edit it here.
- No taglines, no names of other products in the interface. Map data must keep its attribution (OpenStreetMap ODbL, IGN CC BY 4.0, Terrarium).

## Before finishing a change

1. `python -m pytest` and the node test are green.
2. If the UI changed: `npx vite build`, then open the running app and look at every page in Spanish and English, at desktop and phone width.
3. README.md, README.es.md and docs/ARCHITECTURE.md say what the app can and cannot do now.
