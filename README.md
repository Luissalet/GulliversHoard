# Gulliver's Hoard

Makes printable 3D models of real places. Pick an area on the map; the app downloads the relief and what is on the ground and builds a closed solid with the terrain, the buildings at their height, lakes, rivers and the sea lowered as water, streets and railways engraved or raised, parks, bridges over water and, if you want, contour lines, a frame and a text engraved underneath. The model can be a rectangle, a rounded rectangle, a circle or a hexagon, and it can be cut into tiles that fit the printer bed, with holes for alignment pins. It comes out as one STL, as a 3MF with one coloured part per filament (terrain, buildings, water, streets, green, frame), one STL and one 3MF per tile, and a 3D preview in the browser. Part of the Hoard family: it works on its own, from the Hoard Hub, from Faustus and from any MCP client.

Lea esto en español: [README.es.md](README.es.md).

![Model](docs/screens/model.png)

## What it does

- **Choose the place.** Search by name (OpenStreetMap's Nominatim), then move the area on the map (drag the centre, resize from the corner, click to recentre). Base maps: OpenStreetMap, OpenTopoMap, the IGN PNOA aerial photo for Spain and a world satellite layer. Everything outside the area is dimmed and the tile seams are drawn on it.
- **See what you will get before building.** Scale (1:N), size in millimetres, ground size, tiles and their size, the elevation source that will be used, which streets will be drawn at that scale, warnings (does not fit the bed, buildings too small at this scale, mesh too fine) and a one-click tile suggestion. "Count what is on the map" downloads the map data and says how many buildings, streets, water and green there are, and which landmarks.
- **Three presets.** City (buildings and streets, about 1:3.000 to 1:15.000), Landscape (stronger relief, contour lines, rivers, main roads) and Relief only (terrain and water in one piece). Every parameter can be changed afterwards.
- **Elevation.** In Spain the 5 m MDT of the Instituto Geográfico Nacional (25 m or 200 m for big areas), with Terrarium filling whatever it does not cover; elsewhere the Terrarium tiles (SRTM, EU-DEM, 3DEP and others, about 30 m in Europe, finer in the USA). The grid is smoothed according to the real resolution of the source so 5 m steps and 30 m facets do not show.
- **What is on the ground.** Buildings with their height (`height`, levels, roof levels, or an estimate by type), multipolygons assembled properly (a lake keeps its islands, a building its courtyard), lakes, reservoirs, river areas and docks, rivers and canals drawn as lines, streets by class with their real width (never narrower than the nozzle can print), railways and tram lines, bridges over water as solid decks, parks, gardens, woods and grass. Tunnels are left out.
- **A printable solid.** Real booleans (manifold3d): water is cut at a flat level per piece (lakes stay flat, long rivers step down), buildings stand on the lowest point of their footprint, the frame and the colour layers never meet the terrain on a coincident wall, and every file is checked for open edges before it is offered.
- **Tiles.** Columns × rows of tiles cut from the whole model (rows lettered from the north, columns numbered from the west, the letter engraved under each tile), with horizontal holes across every seam for pins: 1.9 mm holes take a piece of 1.75 mm filament.
- **Colour.** In colour mode the 3MF has one named, coloured part per filament (Terreno, Edificios, Agua, Carreteras, Zonas verdes, Marco); the parts fit each other exactly and slicers import them as one object with parts. The STL is always the same model in one closed piece.
- **3D preview.** Orbit, top and front views, parts that can be hidden, and tiles that can be pulled apart.
- **Library.** Every model keeps its parameters and its last builds; edit, rebuild, duplicate, rename, export the files to a folder, or send them to Vulcan's Hoard (the 3D print library of the family). The tiles ZIP carries a `LEEME.txt` that says how the tiles and pins go together and which sources to credit.
- **Report.** Scale, size, solid volume, an estimate of PLA grams, relief range, buildings drawn, triangles, tiles that fit the bed, the elevation source and its resolution, and the attribution text.

## Running

```
python -m venv venv
venv\Scripts\pip install -r requirements.txt
npm install && npx vite build
venv\Scripts\python -m gulliver_hoard        # http://127.0.0.1:5206
```

`GULLIVER_FAKE=1` builds an invented town (hills, a river with a bridge, a lake with an island, blocks of buildings) without the network, for trying the interface. `GULLIVER_DATA_DIR` moves the data folder (projects, builds, the download cache).

Without the app: `python -m gulliver_hoard.cli --place "Toledo, España" --radius-km 1.5 --tiles 2x2 --out toledo` writes the same files into a folder.

The MCP bridge is `python mcp_server.py` (20 tools; `faustus-plugin.json` describes it for Faustus and the Hoard Hub). `model_create` with `lon`, `lat`, the ground size and a preset is enough to get a model; `area_check` answers without building anything. Deleting a model and clearing the cache need `confirm=true`.

## Family

- Finished models are announced as `gulliver.model.ready` and, when "File every model in Vulcan" is on, as `gulliver.export.done` with the path of the 3MF: the hub rule `rule-exports-to-vulcan` files them in Vulcan's Hoard with a reference back to the model (`hoard://gulliver/model/<id>`). "Send to Vulcan" does the same on demand (also the tiles).
- The Faustus panel (the floating Faustus button) sees what is open: the models of the library, the model on its page (status, scale, size, tiles, centre, ground size) and, in the new-model and settings forms, the fields typed and not saved yet (`gulliver:context-request`). When the assistant finishes a turn the page reloads what it shows and keeps what you are typing.

## What it cannot do

- The models are as good as the open data: buildings without `height` or levels in OpenStreetMap get an estimate by type, roofs are flat, monuments are their footprint extruded (no domes or towers), and bridges are drawn only over water. It does not use photogrammetry or the photorealistic 3D tiles of commercial map services (their terms forbid making products from them).
- Outside Spain the relief is about 30 m per sample in most of the world: fine for landscapes, coarse for a small town on a slope.
- The sea is whatever is at or below 0 m and touches the edge of the area; land below sea level that touches the edge (polders) is taken as sea. Turn "Sea" off there.
- Bridges that do not cross water, overpasses and tunnels are not modelled (tunnels are left out; their streets are not engraved).
- Very large areas with buildings depend on the Overpass servers answering: hundreds of km² of a city may time out. The mesh has a limit of 3 million cells; past it the app asks for a coarser resolution.
- 3MF colours are names and display colours: the slicer still asks which filament goes to which part.

## Development

```
python -m pytest                            # backend, against the invented town
node --test client/copilot-context.test.mjs  # the panel context
npx vite build                              # interface into gulliver_hoard/static
```

Data: elevation © Instituto Geográfico Nacional (CC BY 4.0) and Mapzen/AWS Terrain Tiles; map data © OpenStreetMap contributors (ODbL). See [NOTICE](NOTICE). MIT licence.
