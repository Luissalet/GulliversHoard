# Gulliver's Hoard

Hace maquetas 3D imprimibles de sitios reales. Eliges una zona en el mapa; la app descarga el relieve y lo que hay sobre el terreno y construye un sólido cerrado con el terreno, cada edificio con su altura (en España desde el Catastro, parte a parte, con sus plantas reales), tejados, torres y agujas donde están mapeados, cada árbol que se ve en la foto aérea, lagos, estanques, ríos y mar rebajados como agua, calles cortadas por su contorno real, vías de tren con su balasto, caminos en los parques y sendas en la montaña, puentes sobre arcos encima del agua y, en la montaña, roca y nieve; y si quieres, curvas de nivel, un marco y un texto grabado debajo. La maqueta puede ser un rectángulo, un rectángulo redondeado, un círculo o un hexágono, y se puede partir en piezas que caben en la cama de la impresora, con agujeros para pines de alineación. Sale como un STL, como un 3MF con una parte de color por filamento (terreno, edificios, agua, calles, vías, verde, árboles, roca, nieve, marco), un STL y un 3MF por pieza, y una vista 3D en el navegador. Es parte de la familia Hoard: funciona sola, desde Hoard Hub, desde Faustus y desde cualquier cliente MCP.

Read this in English: [README.md](README.md).

![Maqueta](docs/screens/model.png)

## Qué hace

- **Elegir el sitio.** Buscas por nombre (Nominatim de OpenStreetMap) y mueves la zona en el mapa (arrastras el centro, cambias el tamaño desde la esquina, pulsas para recentrar). Mapas base: OpenStreetMap, OpenTopoMap, la ortofoto PNOA del IGN para España y un satélite mundial. Lo que queda fuera de la zona se oscurece y se dibujan los cortes entre piezas.
- **Ver lo que saldrá antes de generar.** Escala (1:N), tamaño en milímetros, tamaño del terreno, piezas y su tamaño, la fuente de relieve que se usará, qué calles se dibujan a esa escala, avisos (no cabe en la cama, edificios demasiado pequeños a esa escala, malla demasiado fina) y la propuesta de piezas con un clic. «Contar lo que hay en el mapa» descarga los datos y dice cuántos edificios, calles, agua y zonas verdes hay y qué edificios destacan.
- **Tres preajustes.** Ciudad (edificios, calles y árboles, de 1:3.000 a 1:15.000 aprox.), Paisaje (relieve, curvas de nivel, ríos, carreteras principales, sendas, bosques, roca, nieve y campo verde) y Solo relieve (terreno y agua en una pieza). Todo se puede cambiar después.
- **Relieve.** En España el MDT de 5 m del Instituto Geográfico Nacional (25 m o 200 m para zonas grandes), con Terrarium donde no llega; fuera, las teselas Terrarium (SRTM, EU-DEM, 3DEP y otras; unos 30 m en Europa, más fino en EE. UU.). La malla se suaviza según la resolución real de la fuente para que no se noten los escalones de 1 m ni las facetas de 30 m.
- **Edificios.** En España (salvo Navarra y el País Vasco, que tienen catastro propio) salen del servicio INSPIRE del Catastro: cada parte de edificio con su huella exacta y sus plantas sobre rasante, así que un bloque con un ala más baja o una torre sobre un zócalo sale escalonado como es. Fuera, de OpenStreetMap: `height`, plantas, plantas de cubierta o una estimación por tipo. Encima de cualquiera de los dos, lo que OpenStreetMap tiene en 3D: partes de edificio (torres, una aguja sobre su torre, una cúpula sobre su tambor, colocadas sobre lo que tienen debajo) y tejados — a dos aguas, a cuatro aguas, en copete, mansarda, a un agua, pirámides y agujas, conos, cúpulas, bulbos y bóvedas; con «Tejados: automático» las casas y las iglesias sin tejado mapeado lo llevan inclinado. Los edificios pequeños se conservan si forman parte de una manzana con tamaño imprimible (una hilera de adosados) y solo se descartan cuando están solos; las torres y agujas se quedan aunque sean finas.
- **Árboles.** En España, cada árbol que se ve en la ortofoto PNOA del IGN: la foto se lee a medio metro por píxel, las copas son las manchas oscuras y claramente verdes (el césped es más claro, las sombras grises) y cada copa se convierte en un árbol 3D redondeado de su tamaño — en las calles, en las plazas, en los patios. En todas partes, los árboles e hileras mapeados en OpenStreetMap; donde no hay foto, los bosques se rellenan densos y los parques salpicados. Los árboles son una parte (y un color) propia y se apoyan en el suelo que tienen debajo.
- **Agua y el resto del terreno.** Lagos, embalses, estanques, piscinas y fuentes, ríos anchos y dársenas, multipolígonos bien montados (un lago conserva sus islas, un edificio su patio), ríos y canales como línea; calles por tipo con su anchura real (nunca más estrechas de lo que imprime la boquilla), con las cuñas estrechas entre calles casi pegadas y las isletas demasiado pequeñas para imprimir convertidas en calzada; vías de tren como balasto elevado con su color (los tranvías van por la calle); caminos dentro de parques y bosques recortados del verde para que se vea el suelo (en campo abierto, todas las sendas, también sobre la roca y la nieve); puentes sobre el agua como tablero sobre pilas y arcos de medio punto (también los de tren), con una sola altura para los tableros que se tocan; los que pasan sobre calles, vías o barrancos, como un tablero más fino sobre el hueco que el modelo de elevación muestra debajo, así la calle de abajo queda libre; azudes y presas como un murete de orilla a orilla. Los túneles se omiten.
- **Montaña.** Nieve por encima de una cota automática en cumbres de más de 2.000 m (o la que pongas) y en los glaciares mapeados, roca donde la ortofoto PNOA muestra piedra desnuda en España (prado y bosque donde los muestra, roca por pendiente donde no se ve claro; fuera de España, o con la opción apagada, en las pendientes más fuertes que el ángulo elegido) y donde OpenStreetMap marca roca o canchal, árboles en los bosques que se ven en la foto, y, con «Todo el campo en verde», verde en todo lo demás. La exageración vertical se rebaja si el relieve fuera a ser más alto que una fracción del ancho de la maqueta (una cordillera con la exageración de una ciudad es un bosque de agujas).
- **Un sólido imprimible.** Booleanos de verdad (manifold3d). Calles, caminos y curvas de nivel se cortan por su contorno real (el corte es el prisma del contorno por encima de una copia del terreno bajada la profundidad) y la costa se traza desde el relieve con marching squares, así que nada sale escalonado según la rejilla del terreno. El agua se corta hasta una superficie continua (lagos, embalses y estanques planos; los ríos bajan suavemente a lo largo de su curso, sin escalones de orilla a orilla), los edificios se apoyan en el punto más bajo de su planta, las capas de color se quedan a un pelo de las paredes de los edificios que tienen encima, los cuerpos de una parte que solo se tocarían por una arista se separan, y cada archivo se comprueba (aristas abiertas) antes de ofrecerlo; el 3MF mantiene separados todos los vértices (seis decimales, nueve cifras si hace falta) para que el laminador lea las mismas partes cerradas.
- **Piezas.** Columnas × filas cortadas de la maqueta entera (filas con letra desde el norte, columnas con número desde el oeste, la letra grabada debajo de cada pieza), con agujeros horizontales en cada junta para pines: los de 1,9 mm admiten un trozo de filamento de 1,75 mm.
- **Color.** En modo color el 3MF lleva una parte con nombre y color por filamento (Terreno, Edificios, Agua, Carreteras, Vías del tren, Zonas verdes, Árboles, Roca, Nieve, Marco — solo las que tenga la maqueta); encajan exactamente y los laminadores las importan como un objeto con partes. El STL es siempre la misma maqueta en una sola pieza cerrada.
- **Vista 3D.** Órbita, planta y alzado, luz de sol con sombras, partes que se pueden ocultar y piezas que se separan.
- **Generación en segundo plano.** Cada generación corre en su propio proceso, así que la app, el panel de Faustus y las herramientas responden mientras se construye una ciudad; cancelar termina ese proceso. Un centro de ciudad de 5 km² con Catastro y árboles de la foto tarda de tres a cuatro minutos (la primera vez, además, unos dos minutos de descargas, luego en caché).
- **Biblioteca.** Cada maqueta guarda sus parámetros y sus últimas generaciones; editar, regenerar, duplicar, renombrar, exportar los archivos a una carpeta o enviarlos a Vulcan's Hoard (la biblioteca de impresión 3D de la familia). El ZIP de piezas lleva un `LEEME.txt` que explica cómo se montan las piezas y los pines y qué fuentes citar.
- **Informe.** Escala, tamaño, volumen, gramos de PLA estimados, rango de relieve, edificios dibujados, triángulos, piezas que caben en la cama, la fuente de relieve y su resolución, y el texto de atribución.

## Arrancar

```
python -m venv venv
venv\Scripts\pip install -r requirements.txt
npm install && npx vite build
venv\Scripts\python -m gulliver_hoard        # http://127.0.0.1:5206
```

`GULLIVER_FAKE=1` genera un pueblo inventado (colinas, un río con puentes de carretera y de tren, un lago con isla, manzanas de edificios, una iglesia con aguja, un parque con caminos, estanque y árboles) sin red, para probar la interfaz. `GULLIVER_DATA_DIR` cambia la carpeta de datos (proyectos, generaciones, caché de descargas).

Sin la app: `python -m gulliver_hoard.cli --place "Toledo, España" --radius-km 1.5 --tiles 2x2 --out toledo` escribe los mismos archivos en una carpeta.

El puente MCP es `python mcp_server.py` (20 herramientas; `faustus-plugin.json` lo describe para Faustus y Hoard Hub). Con `model_create` y `lon`, `lat`, el tamaño del terreno y un preajuste basta para tener una maqueta; `area_check` contesta sin generar nada. Borrar una maqueta y vaciar la caché piden `confirm=true`.

## Familia

- Las maquetas terminadas se anuncian como `gulliver.model.ready` y, con «Guardar cada maqueta en Vulcan» activado, como `gulliver.export.done` con la ruta del 3MF: la regla `rule-exports-to-vulcan` del hub las archiva en Vulcan's Hoard con una referencia a la maqueta (`hoard://gulliver/model/<id>`). «Enviar a Vulcan» hace lo mismo a demanda (también las piezas).
- El panel de Faustus (el botón flotante de Faustus) ve lo que está abierto: las maquetas de la biblioteca, la maqueta de su página (estado, escala, tamaño, piezas, centro, tamaño del terreno) y, en los formularios de nueva maqueta y ajustes, los campos escritos y sin guardar (`gulliver:context-request`). Al terminar un turno la página recarga lo que muestra y conserva lo que estás escribiendo.

## Qué no puede hacer

- Las maquetas son tan buenas como los datos abiertos. Fuera de España, los edificios sin `height` ni plantas en OpenStreetMap llevan una altura estimada por tipo; los tejados y las partes 3D solo aparecen donde alguien los mapeó (o, con «automático», como tejado inclinado en casas e iglesias). No usa fotogrametría ni las teselas 3D fotorrealistas de los servicios de mapas comerciales (sus condiciones prohíben hacer productos con ellas).
- Los árboles se ven en la foto solo en España y solo a escalas más finas que 1:15.000 aprox. (más lejos una copa es más pequeña que el árbol imprimible más pequeño y los bosques se rellenan). La detección es una regla de color, no un modelo entrenado: un seto oscuro puede pasar por árbol y un árbol a la sombra de un edificio se puede escapar.
- El Catastro contesta despacio la primera vez (unos dos minutos para una ciudad de 5 km², luego en caché) y no tiene datos de Navarra ni del País Vasco; allí los edificios salen de OpenStreetMap.
- Fuera de España el relieve tiene unos 30 m por muestra en casi todo el mundo: bien para paisajes, grueso para un pueblo pequeño en ladera.
- El mar es lo que está a 0 m o menos y toca el borde de la zona; los pólderes bajo el nivel del mar que tocan el borde se toman por mar. Ahí, desactiva «Mar».
- No se modelan los puentes que no cruzan agua, los pasos elevados ni los túneles (los túneles se omiten y sus calles no se graban). Los arcos necesitan sitio: un tablero a menos de unas décimas de milímetro sobre el agua sale como pasarela maciza.
- Las zonas muy grandes con edificios dependen de que respondan los servidores Overpass: cientos de km² de ciudad pueden agotar el tiempo. La malla tiene un límite de 3 millones de celdas; por encima la app pide una resolución más gruesa.
- Los colores del 3MF son nombres y colores de vista: el laminador sigue preguntando qué filamento va a cada parte.

## Desarrollo

```
python -m pytest                            # backend, contra el pueblo inventado
node --test client/copilot-context.test.mjs  # el contexto del panel
npx vite build                              # la interfaz en gulliver_hoard/static
```

Datos: relieve y ortofoto © Instituto Geográfico Nacional (CC BY 4.0) y Mapzen/AWS Terrain Tiles; edificios en España © Dirección General del Catastro; datos del mapa © colaboradores de OpenStreetMap (ODbL). Ver [NOTICE](NOTICE). Licencia MIT.
