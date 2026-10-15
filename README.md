# Gulliver's Hoard

Makes printable 3D models of real places. Pick an area on the map; the app downloads the relief and what is on the ground and builds a closed solid with the terrain, every building at its height (in Spain from the cadastre, part by part, with its real number of floors), roofs, towers and spires where they are mapped, every tree it can see on the aerial photo, lakes, ponds, rivers and the sea lowered as water, streets cut along their real outline, railway beds, footpaths through the parks, bridges standing on arches over the water, and on mountains bare rock and snow; if you want, also contour lines, a frame and a text engraved underneath. The model can be a rectangle, a rounded rectangle, a circle or a hexagon, and it can be cut into tiles that fit the printer bed, with holes for alignment pins. It comes out as one STL, as a 3MF with one coloured part per filament (terrain, buildings, water, streets, railways, green, trees, rock, snow, frame), one STL and one 3MF per tile, and a 3D preview in the browser. Part of the Hoard family: it works on its own, from the Hoard Hub, from Faustus and from any MCP client.

Lea esto en español: [README.es.md](README.es.md).

![Model](docs/screens/model.png)

## What it does

- **Choose the place.** Search by name (OpenStreetMap's Nominatim), then move the area on the map (drag the centre, resize from the corner, click to recentre). Base maps: OpenStreetMap, OpenTopoMap, the IGN PNOA aerial photo for Spain and a world satellite layer. Everything outside the area is dimmed and the tile seams are drawn on it.
- **See what you will get before building.** Scale (1:N), size in millimetres, ground size, tiles and their size, the elevation source that will be used, which streets will be drawn at that scale, warnings (does not fit the bed, buildings too small at this scale, mesh too fine) and a one-click tile suggestion. "Count what is on the map" downloads the map data and says how many buildings, streets, water and green there are, and which landmarks.
- **Three presets.** City (buildings, streets, trees, about 1:3.000 to 1:15.000), Landscape (relief, contour lines, rivers, main roads, woods, rock, snow and green fields) and Relief only (terrain and water in one piece). Every parameter can be changed afterwards.
- **Elevation.** In Spain the 5 m MDT of the Instituto Geográfico Nacional (25 m or 200 m for big areas), with Terrarium filling whatever it does not cover; elsewhere the Terrarium tiles (SRTM, EU-DEM, 3DEP and others, about 30 m in Europe, finer in the USA). The grid is smoothed according to the real resolution of the source so 5 m steps and 30 m facets do not show.
- **Buildings.** In Spain (except Navarre and the Basque Country, which keep their own cadastres) they come from the cadastre's INSPIRE service: every building part with its exact footprint and its floors above ground, so a block with a lower wing or a tower on a podium comes out stepped as it is. Elsewhere, OpenStreetMap: `height`, levels, roof levels or an estimate by type. On top of either, what OpenStreetMap maps in 3D: building parts (towers, a spire on its tower, a dome on its drum, drawn floating on what is under them), and roofs — gabled, hipped, half-hipped, mansard, lean-to, pyramids and spires, cones, domes, onions and barrels; with "Roofs: auto" houses and churches without a mapped roof get a pitched one. Small buildings are kept when they belong to a block big enough to print (a row of terraced houses) and dropped only when they stand alone; towers and spires stay however slim.
- **Trees.** In Spain every tree seen on the national aerial photo (PNOA, IGN): the photo is read at half a metre a pixel, crowns are the dark, clearly green blobs (lawns are paler, shadows grey), and each crown becomes a rounded 3D tree of its size — along the streets, in the squares, in the courtyards. Everywhere, the trees and rows of trees mapped in OpenStreetMap; where there is no photo, woods are filled densely and parks scattered. Trees are their own part (and colour) and stand on whatever ground is under them.
- **Water and the rest of the ground.** Lakes, reservoirs, ponds, pools and fountains, river areas and docks, multipolygons assembled properly (a lake keeps its islands, a building its courtyard), rivers and canals drawn as lines; streets by class with their real width (never narrower than the nozzle can print), narrow slivers between nearly touching streets and traffic islands too small to print merged into the street; railways as raised ballast beds in their own colour (trams run in the street); footpaths inside parks and woods cut out of the green so the ground shows through; bridges over water as a deck on piers and round arches (rail bridges too), touching decks sharing one height. Tunnels are left out.
- **Mountains.** Snow above an automatic snow line on summits over 2,000 m (or the one you set) and on mapped glaciers, bare rock on slopes steeper than the set angle and where OpenStreetMap maps rock or scree, and, with "All open land green", green everywhere else. The vertical exaggeration is lowered when the relief would be taller than a set share of the model's width (a range drawn at a city's exaggeration is a forest of needles).
- **A printable solid.** Real booleans (manifold3d). Streets, paths and contour lines are cut along their real outlines (the cut is the outline's prism above a copy of the terrain lowered by the depth), and the coastline is traced from the elevation with marching squares, so nothing is stepped along the terrain grid. Water is cut at a flat level per piece (lakes stay flat, long rivers step down), buildings stand on the lowest point of their footprint, colour layers stay a hair off the walls of the buildings standing on them, bodies of one part that would only touch along an edge are parted, and every file is checked for open edges before it is offered.
- **Tiles.** Columns × rows of tiles cut from the whole model (rows lettered from the north, columns numbered from the west, the letter engraved under each tile), with horizontal holes across every seam for pins: 1.9 mm holes take a piece of 1.75 mm filament.
- **Colour.** In colour mode the 3MF has one named, coloured part per filament (Terreno, Edificios, Agua, Carreteras, Vías del tren, Zonas verdes, Árboles, Roca, Nieve, Marco — only the ones the model has); the parts fit each other exactly and slicers import them as one object with parts. The STL is always the same model in one closed piece.
- **3D preview.** Orbit, top and front views, sunlight with shadows, parts that can be hidden, and tiles that can be pulled apart.
- **Builds in the background.** Each build runs in its own process, so the app, the Faustus panel and the agent tools answer while a city is being built; cancelling a build ends that process. A city centre of 5 km² with the cadastre and the aerial trees takes three to four minutes (the first time also two minutes of downloads, then cached).
- **Library.** Every model keeps its parameters and its last builds; edit, rebuild, duplicate, rename, export the files to a folder, or send them to Vulcan's Hoard (the 3D print library of the family). The tiles ZIP carries a `LEEME.txt` that says how the tiles and pins go together and which sources to credit.
- **Report.** Scale, size, solid volume, an estimate of PLA grams, relief range, buildings drawn, triangles, tiles that fit the bed, the elevation source and its resolution, and the attribution text.

## Running

```
python -m venv venv
venv\Scripts\pip install -r requirements.txt
npm install && npx vite build
venv\Scripts\python -m gulliver_hoard        # http://127.0.0.1:5206
```

`GULLIVER_FAKE=1` builds an invented town (hills, a river with road and rail bridges, a lake with an island, blocks of buildings, a church with a spire, a park with paths, a pond and trees) without the network, for trying the interface. `GULLIVER_DATA_DIR` moves the data folder (projects, builds, the download cache).

Without the app: `python -m gulliver_hoard.cli --place "Toledo, España" --radius-km 1.5 --tiles 2x2 --out toledo` writes the same files into a folder.

The MCP bridge is `python mcp_server.py` (20 tools; `faustus-plugin.json` describes it for Faustus and the Hoard Hub). `model_create` with `lon`, `lat`, the ground size and a preset is enough to get a model; `area_check` answers without building anything. Deleting a model and clearing the cache need `confirm=true`.

## Family

- Finished models are announced as `gulliver.model.ready` and, when "File every model in Vulcan" is on, as `gulliver.export.done` with the path of the 3MF: the hub rule `rule-exports-to-vulcan` files them in Vulcan's Hoard with a reference back to the model (`hoard://gulliver/model/<id>`). "Send to Vulcan" does the same on demand (also the tiles).
- The Faustus panel (the floating Faustus button) sees what is open: the models of the library, the model on its page (status, scale, size, tiles, centre, ground size) and, in the new-model and settings forms, the fields typed and not saved yet (`gulliver:context-request`). When the assistant finishes a turn the page reloads what it shows and keeps what you are typing.

## What it cannot do

- The models are as good as the open data. Outside Spain, buildings without `height` or levels in OpenStreetMap get an estimate by type; roofs and 3D parts appear only where someone mapped them (or, with "auto", as pitched roofs on houses and churches). It does not use photogrammetry or the photorealistic 3D tiles of commercial map services (their terms forbid making products from them).
- Trees are seen on the photo only in Spain, and only finer than about 1:15.000 (further out a single crown is smaller than the smallest printable tree, so woods are filled instead). The detection is a colour rule, not a trained model: dark hedges can pass for trees and a tree in a building's shadow can be missed.
- The cadastre answers slowly the first time (about two minutes for a 5 km² city, then cached) and has no data for Navarre and the Basque Country; there the buildings come from OpenStreetMap.
- Outside Spain the relief is about 30 m per sample in most of the world: fine for landscapes, coarse for a small town on a slope.
- The sea is whatever is at or below 0 m and touches the edge of the area; land below sea level that touches the edge (polders) is taken as sea. Turn "Sea" off there.
- Bridges that do not cross water, overpasses and tunnels are not modelled (tunnels are left out; their streets are not engraved). Arches need room: a deck lower than a few tenths of a millimetre over the water is drawn as a solid causeway.
- Very large areas with buildings depend on the Overpass servers answering: hundreds of km² of a city may time out. The mesh has a limit of 3 million cells; past it the app asks for a coarser resolution.
- 3MF colours are names and display colours: the slicer still asks which filament goes to which part.

## Development

```
python -m pytest                            # backend, against the invented town
node --test client/copilot-context.test.mjs  # the panel context
npx vite build                              # interface into gulliver_hoard/static
```

Data: elevation and aerial photo © Instituto Geográfico Nacional (CC BY 4.0) and Mapzen/AWS Terrain Tiles; buildings in Spain © Dirección General del Catastro; map data © OpenStreetMap contributors (ODbL). See [NOTICE](NOTICE). MIT licence.
