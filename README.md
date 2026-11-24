# OpEdTagger — Nueva arquitectura

**Versión de desarrollo: 1.5.2-series-navigation-loading-guard**

OpEdTagger analiza episodios de anime para detectar segmentos Opening (OP) y Ending (ED), permite revisar los timestamps y ofrece un flujo separado para inyectar capítulos en archivos MKV.

Esta distribución combina el motor de análisis del proyecto original con la interfaz visual del ZIP 2. El backend se ha reorganizado alrededor de servicios, SQLite y una cola local de un único worker.

> **Estado:** base funcional de nueva arquitectura. La API, persistencia, migración y lógica de validación tienen pruebas automatizadas. La integración real con AnimeThemes/FFmpeg/MKVToolNix y el empaquetado final de Windows requieren validación en un equipo Windows con las herramientas instaladas. No se afirma que esta versión esté lista para producción.

## Principios de seguridad

- Analizar un MKV **no modifica el archivo original**.
- Los resultados de análisis, revisión e inyección tienen estados independientes.
- La inyección requiere revisión aprobada y confirmación explícita.
- Antes de modificar un MKV se crea una copia de seguridad.
- Si la inyección falla, el sistema intenta restaurar la copia.
- Si la aplicación se interrumpe durante la inyección, el registro pasa a `RECOVERY_REQUIRED`; no se repite automáticamente.
- Si FFprobe no puede determinar la duración, no se genera una vista previa ni se inyectan capítulos con una duración inventada.
- La API se enlaza a `127.0.0.1`, no a todas las interfaces de red.

## Arquitectura

```text
frontend/              React 18 + Vite (interfaz del ZIP 2)
backend/api/            FastAPI routers y schemas
backend/services/       Casos de uso: biblioteca, análisis, jobs, templates y logs
backend/infrastructure/ Configuración, SQLite y logging
core/                   Algoritmo OP/ED y herramientas multimedia
migration/              Herramientas y carpeta local ignorada para datos legacy
scripts/                Herramientas de migración
tests/                  Pruebas automatizadas
```

- **Frontend:** mantiene la composición y los estilos de ZIP 2; Dashboard, detalle de serie, análisis individual, Logs y Settings consumen la API local. La interfaz de origen está escrita principalmente en JSX/JavaScript; se incorporan contratos TypeScript iniciales (`src/api/types.ts`), pero la migración integral a TSX y el type-check aún quedan como tarea de la siguiente fase.
- **Backend:** FastAPI.
- **Persistencia:** SQLite con WAL y claves foráneas.
- **Trabajos:** cola local persistida con un worker. Los trabajos activos se marcan como interrumpidos al iniciar después de un cierre inesperado; no se repiten solos.
- **Progreso:** polling REST desde la interfaz.
- **Motor:** conserva el algoritmo chroma del original (`COSINE_SIMILARITY_THRESHOLD = 0.68`). El detector OP/ED se trata como componente protegido: la preparación de herramientas, las plantillas, la interfaz y la inyección no deben cambiar su lógica ni sus parámetros. Una prueba de contrato verifica sus constantes y pasos críticos. El valor `score` se presenta como score bruto, nunca como porcentaje ni probabilidad. Los WAV derivados tienen un manifiesto de fingerprints: se regeneran cuando cambia el video fuente y se eliminan si el video fuente desaparece.
- **Templates:** consulta AnimeThemes; si no hay coincidencia exacta única exige seleccionar el anime. En Detalle de serie se puede buscar y vincular manualmente la entrada correcta; las descargas seleccionadas se preparan en un directorio staging antes de reemplazar la caché anterior. La descarga mantiene `songs_files` y sus WAV manuales; el siguiente análisis regenera solo las conversiones derivadas cuyo vídeo fuente haya cambiado.

## Requisitos

- Windows 10/11 (objetivo prioritario).
- Python 3.11 o superior.
- Node.js 20 o superior y npm, necesarios para instalar/build del frontend desde el código fuente.
- En Windows x64, FFmpeg/FFprobe y MKVToolNix se preparan automáticamente en `plugins/` durante el primer arranque. En otros sistemas, deben estar instalados en `plugins_path` o en `PATH`.

En Windows x64, el backend prepara automáticamente FFmpeg/FFprobe y MKVToolNix al iniciar cuando faltan en `plugins_path`. Las descargas se verifican mediante SHA-256, se extraen en un directorio temporal con validación de rutas y solo se activan después de superar una comprobación de versión. Si la instalación falla, el backend sigue disponible para las funciones que no dependan de esas herramientas y comunica el error en Settings. En otras plataformas se requiere que estén instaladas en `plugins_path` o en `PATH`.
- Conexión a Internet para consultar AnimeThemes y descargar templates.

La detección puede ejecutarse con FFmpeg y templates ya disponibles. La escritura de capítulos necesita además MKVToolNix.

## Instalación en Windows

1. Descomprime la carpeta `OpEdTagger`.
2. Instala Python 3.11+ y Node.js 20+.
3. Abre PowerShell en la carpeta `OpEdTagger`. Para permitir la ejecución de estos scripts solo en la sesión actual (sin cambiar la política permanentemente), ejecuta `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`.
4. Ejecuta `.\setup_windows.ps1`. Como alternativa, haz doble clic en `setup_windows.bat`, que abre el asistente en Windows Terminal con PowerShell 7 (`pwsh`) cuando está disponible.
5. Cuando termine, ejecuta `.\run_windows.ps1` desde esa misma terminal para mantener el servidor en tu sesión de PowerShell 7. También puedes hacer doble clic en `run_windows.bat` para abrir una pestaña de Windows Terminal con PowerShell 7.
6. Abre `http://127.0.0.1:8000` en el navegador.
7. En Settings, configura la ruta absoluta de la biblioteca, la carpeta de templates, las herramientas multimedia y los respaldos.
8. Pulsa **Analizar biblioteca** para indexar las carpetas y sus archivos MKV.

La biblioteca debe tener una subcarpeta por serie. Los archivos `.mkv` se descubren recursivamente dentro de cada carpeta.

Para desarrollo, ejecuta `.\dev_windows.ps1` desde PowerShell después de la instalación. Vite se abrirá en una segunda pestaña de Windows Terminal con PowerShell 7 en `http://127.0.0.1:5173` y el backend permanecerá en la terminal actual en `http://127.0.0.1:8000`.

## Nota sobre Node.js y npm en Windows

El asistente `setup_windows.ps1` detecta `node.exe` y `npm.cmd` por separado. Esto evita depender de `where npm`, que puede no encontrar `npm.cmd` cuando PowerShell resuelve `npm.ps1`, o cuando el PATH de la sesión está desactualizado. El setup muestra la ruta y versión detectadas de Node.js y npm antes de instalar dependencias. Se admite Node.js 20 o superior (incluida la rama 26). Si Node.js se detecta pero npm no, el mensaje indicará que debe repararse la instalación de Node.js incluyendo npm.

Los archivos `.bat` son accesos rápidos compatibles con doble clic y priorizan Windows Terminal + PowerShell 7 (`wt.exe pwsh.exe`). Si Windows Terminal no está disponible, usan una ventana independiente de PowerShell 7. Para ejecutar directamente dentro de una sesión que ya tengas abierta, usa los scripts `.ps1` indicados arriba.

## Ubicación de datos

La aplicación separa los datos de ejecución de los recursos de trabajo. Configuración, SQLite, respaldos, cargas temporales y logs se guardan en el directorio de datos del usuario:

- Windows: `%LOCALAPPDATA%\OpEdTagger`
- macOS: `~/Library/Application Support/OpEdTagger`
- Linux: `$XDG_DATA_HOME/OpEdTagger` o `~/.local/share/OpEdTagger`

Las carpetas `Templates/` y `plugins/` se encuentran en la raíz del proyecto. Cada serie utiliza `Templates/<serie>/video_files` para los vídeos OP/ED y `Templates/<serie>/songs_files` para los WAV derivados. Las herramientas multimedia se instalan en `plugins/`. Estas carpetas están excluidas de Git.

Se pueden sobrescribir las ubicaciones mediante `OPEDTAGGER_DATA_DIR` y `OPEDTAGGER_CONFIG_PATH`. Al detectar las rutas predeterminadas de versiones anteriores, OpEdTagger copia los archivos existentes a las nuevas ubicaciones sin sobrescribir archivos de destino ni borrar las carpetas de origen. La migración se marca como completada para no recorrer repetidamente bibliotecas grandes.

La biblioteca del usuario no se copia al directorio de datos. Las cargas del análisis individual se almacenan temporalmente y se eliminan al finalizar el trabajo.

## Uso recomendado

### Procesar una serie

1. Configura e indexa la biblioteca.
2. Abre una serie y pulsa **Analizar serie**.
3. El worker reutiliza resultados de caché únicamente cuando coinciden la versión del analizador, la firma de templates, el tamaño, la fecha de modificación y el fingerprint muestreado del archivo.
4. Revisa los episodios con coincidencias.
5. Ajusta manualmente los timestamps si es necesario y guarda los cambios.
6. Pulsa **Aprobar detecciones**.
7. Para inyectar, revisa la vista previa y confirma. Si el MKV ya tiene capítulos, se solicitará una segunda confirmación para reemplazarlos.

Los episodios sin coincidencias se conservan como `NO_MATCH`, no como errores ni como análisis exitosos con datos vacíos.

### Análisis individual

La pantalla permite seleccionar un MKV, consultar AnimeThemes y elegir un resultado para ejecutar el análisis. El archivo se carga al backend local para el análisis, y la copia temporal se elimina al terminar.

**Limitación conocida de esta versión:** el análisis individual no se incorpora al catálogo y no ofrece todavía el flujo de revisión/inyección. El original cargado no se modifica.

### Selección de templates

Para el procesamiento por serie se reutiliza la carpeta de templates existente. Si está vacía, el servicio intenta una coincidencia exacta única con AnimeThemes. Si hay ambigüedad, el trabajo informa del problema en vez de descargar resultados potencialmente incorrectos. La API también expone búsqueda y descarga mediante `/api/templates/search` y `/api/templates/download`.

## Migración desde `anime_master.json`

El JSON legacy pertenece a cada usuario y **no se distribuye en el repositorio**. Coloca tu copia local en `migration/local/anime_master.legacy.json` (esa carpeta está excluida de Git). **No se importa automáticamente.** Primero configura `library_path` desde Settings.

Ejecuta una simulación desde la raíz del proyecto:

```powershell
.venv\Scripts\python.exe -m scripts.migrate_legacy_json migration\local\anime_master.legacy.json
```

Revisa el informe de elegibilidad, archivos ausentes y registros incompletos. Solo después ejecuta:

```powershell
.venv\Scripts\python.exe -m scripts.migrate_legacy_json migration\local\anime_master.legacy.json --apply
```

La migración crea una copia `.pre-migration.bak`, registra su hash en SQLite y es idempotente para el mismo archivo. No elimina ni modifica el JSON original. Los resultados legacy se conservan con estado `STALE`: sus timestamps pueden consultarse como referencia, pero no se pueden aprobar ni inyectar hasta repetir el análisis con el motor actual y generar un fingerprint verificable del MKV. La migración no presume que los capítulos estén inyectados.

El esquema SQLite registra una versión inicial en `schema_migrations`; los cambios futuros de estructura deben incorporarse mediante migraciones explícitas y no mediante alteraciones manuales de la base de datos.

## Pruebas

Desde la raíz del proyecto:

```powershell
.venv\Scripts\python.exe -m pytest -q
```

Las pruebas cubren validación de timestamps y capítulos XML, lectura posterior de cantidad/títulos/tiempos con FFprobe simulado, rollback de backup simulado, fingerprints, invalidación de WAV derivados, rutas seguras, reconciliación de biblioteca, configuración, esquema SQLite, migración dry-run/idempotente, estados de episodio y endpoints básicos.

Consulta `docs/QA_REPORT.md` para ver qué se ha ejecutado y qué permanece pendiente. Las pruebas automatizadas no sustituyen las pruebas en Windows con archivos MKV representativos. Antes de usar la inyección sobre una biblioteca real, prueba con copias de MKV y confirma manualmente capítulos, pistas, metadatos y respaldo.

## Endpoints principales

La documentación interactiva de FastAPI se encuentra en `http://127.0.0.1:8000/docs` mientras el backend está en ejecución.

- `GET /api/system/health`
- `GET /api/dashboard?scan=false`
- `GET /api/library` y `POST /api/library/scan`
- `GET /api/series/{series_name}`
- `GET /api/jobs`, `POST /api/jobs/series/{series_name}`, `GET /api/jobs/{job_id}`, `POST /api/jobs/{job_id}/cancel`
- `POST /api/templates/search`, `POST /api/templates/download`
- `POST /api/analysis` (multipart MKV)
- `PUT /api/episodes/{episode_id}/review`
- `GET /api/episodes/{episode_id}/chapter-preview`
- `POST /api/episodes/{episode_id}/inject`
- `GET /api/logs`, `GET /api/logs/export`
- `GET/PUT /api/settings`, `GET/PUT /api/settings/preferences`
- `POST /api/artwork/search`, `PUT /api/artwork/series/{series_name}/poster`, `GET/DELETE /api/artwork/series/{series_name}/metadata`

## Limitaciones y trabajo posterior

- AniList está integrado mediante su API pública GraphQL: no requiere token, API key ni configuración en Settings. Desde el Dashboard o el detalle de una serie puedes buscar por el nombre de la carpeta, modificar manualmente el título, elegir una coincidencia o cargar una imagen local. Consulta `docs/ANILIST_ARTWORK.md` para el flujo, caché, atribución y condiciones de uso.
- No se ha creado un instalador ni un ejecutable empaquetado; la estructura está preparada para una fase posterior.
- Los archivos MKV del análisis individual todavía no se pueden revisar/injectar desde esa pantalla.
- La UI de revisión manual está disponible para episodios indexados en una biblioteca; debe probarse visualmente en Windows.
- El seguimiento usa polling; SSE puede evaluarse más adelante si es necesario.
- La cola es de un worker local y no reanuda por sí sola trabajos interrumpidos.
- No se han ejecutado pruebas reales de descarga externa ni de modificación MKV en este entorno.
- El build Vite no se pudo ejecutar aquí porque la instalación npm quedó bloqueada por falta de acceso a registry; `package-lock.json` mantiene la resolución de dependencias del ZIP 2 para reproducir la instalación en Windows.

## Procedencia

- `core/analyzer.py` deriva del motor original del ZIP 1 con cambios acotados para validación de FFmpeg, progreso y cache WAV controlada mediante fingerprints de las fuentes.
- `frontend/` parte de la interfaz del ZIP 2; no se conserva su `mockData.js` ni sus datos simulados.
- `legacy/` contiene copias de referencia del procesador y herramientas MKV antiguas. No se importan en el backend activo.
- Los ZIP originales proporcionados por el usuario no se modifican.

## Organización de la biblioteca

OpEdTagger conserva la jerarquía de carpetas que el usuario ya tiene en disco. No intenta inferir temporadas, cours, arcos ni categorías a partir de AniList o AnimeThemes. El detalle de cada serie muestra todos los niveles de subcarpetas con sus nombres originales; los MKV ubicados directamente en la carpeta de la serie aparecen bajo «Raíz de la serie».

Ejemplo válido: `Serie/S01/BD/Episode 01.mkv` y `Serie/Final Arc/Part 2/Episode 02.mkv`. La jerarquía puede tener cualquier profundidad y se actualiza al volver a escanear la biblioteca.



## AniList artwork and series metadata

OpEdTagger searches the public AniList GraphQL catalogue without an API key, token or account. From a Dashboard row or series detail page, open the artwork dialog to search using the folder name, edit the query manually, select a matching entry, or upload a local image. The selected poster is cached locally and referenced in SQLite, so it remains available offline.

When a series is linked to an AniList entry, its detail page also retrieves the synopsis, alternate titles, year, format, country, genres, external episode count, cover and optional panoramic banner. The poster opens a larger detail lightbox. Catalogue metadata is fetched on demand and cached temporarily in process memory (15 minutes, up to 40 entries); only the explicitly selected AniList media ID is persisted in SQLite. The banner is used as a very low-opacity background in the series hero only, keeping episode rows and folder navigation readable.

AniList never changes the user's folder hierarchy or local episode counts. See [docs/ANILIST_ARTWORK.md](docs/ANILIST_ARTWORK.md) for routes, metadata handling, attribution and acceptable-use considerations.


## Navegación entre series sin contenido obsoleto (1.5.2)

- Al seleccionar una serie distinta, el detalle anterior se limpia en el mismo ciclo de actualización de React y se muestra un estado de carga neutro con el nombre de destino. La portada, banner, sinopsis, estadísticas y episodios anteriores no permanecen visibles durante la solicitud.
- Los detalles se etiquetan con el nombre de la serie a la que pertenecen; el componente de detalle solo se monta cuando la etiqueta coincide con la selección actual.
- Una secuencia de solicitudes descarta respuestas fuera de orden. Si una petición anterior termina después de una navegación más reciente (por ejemplo, Dan Da Dan y luego Dr. STONE), no puede sobrescribir los datos actuales.
- Si la carga falla, se presenta el error de la serie de destino con opciones para reintentar o volver al Dashboard.
- Se añadió una prueba automatizada del control de secuencia y el comando `npm test` para ejecutarla desde `frontend`.
- La prueba se ejecuta con `npm test` desde la carpeta `frontend`; utiliza el ejecutor nativo de Node.js y no requiere dependencias adicionales.
