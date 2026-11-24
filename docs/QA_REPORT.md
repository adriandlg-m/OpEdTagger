# Informe de validación técnica

**Versión:** 1.5.2-recover-cli-workflows  
**Ámbito:** revisión estática, pruebas unitarias y pruebas de API locales en el entorno de construcción.

## Comprobaciones realizadas

| Comprobación | Resultado |
|---|---|
| `python -m compileall -q backend core scripts` | Correcto |
| `PYTHONPATH=. pytest -q` | **94 pruebas aprobadas**, 1 warning, GitHub Actions (Python 3.12, Ubuntu) |
| TypeScript Compiler API parser sobre JS/JSX/TS del frontend | 17 archivos analizados sin errores de sintaxis |
| Jerarquía recursiva de carpetas de usuario | Verificada con carpetas multinivel y archivos en raíz |
| Toast fijo y Dashboard con scroll interno | Revisados estáticamente; prueba visual pendiente en Windows |
| AniList public GraphQL API (sin token) y búsqueda de anime | Cubiertos con respuestas simuladas; una consulta externa real queda pendiente de la prueba en Windows |
| Configuración de AniList | No hay token, API key ni credenciales que configurar |
| Descarga y caché local de pósteres | Cubierta mediante pruebas simuladas de API e imagen |
| Atribución AniList | Aviso de fuente incluido en el modal y documentación; revisar requisitos vigentes de marca/atribución antes de una distribución pública |
| Scroll del árbol de episodios | Se separó el viewport de scroll del contenido flex; prueba visual pendiente en Windows |
| API local con FastAPI TestClient | Health, dashboard, settings y logs responden correctamente |
| Frontend `npm ci` | Correcto en GitHub Actions (Node.js 22) |
| Frontend `npm test` | Correcto en GitHub Actions |
| Frontend `npm run build` | Correcto en GitHub Actions (Vite) |
| Contrato del detector OP/ED | Se conserva `0.68`, el criterio de selección del ganador y el score bruto; se añadieron métricas auxiliares normalizadas sin modificar la aceptación de candidatos |
| Migración de rutas, gestor de plugins y estructura de plantillas | Pruebas unitarias aprobadas en la suite de 94 pruebas |
| Esquema SQLite y persistencia | Verificado mediante pruebas |
| Migración JSON dry-run / apply / idempotencia | Verificada con fixture aislado |
| Exclusión mutua entre análisis e inyección por serie | Verificada mediante índice único de SQLite y migración v5 |
| Recuperación de trabajos interrumpidos | Verificada sin reanudación automática |
| Reconciliación de archivos/series eliminados del catálogo | Verificada con fixtures; solo elimina filas SQLite |
| Limpieza de cargas individuales interrumpidas | Verificada con validación de ruta |
| Estados de inyección tras reanálisis | Verificados: éxito se reinicia; recuperación requerida se conserva |
| Detección de cambios conservando tamaño y mtime | Verificada mediante fingerprint |
| Recuento de migración con registros preexistentes | Verificado sin sobrecontar |
| Fingerprint y detección de cambios en archivos | Verificado con fixtures |
| Validación de timestamps y XML de capítulos | Verificada |
| Restauración del backup tras fallo de verificación | Verificada con sustitución simulada de herramientas externas |
| Recuperación del directorio staging de templates | Verificada con fixtures |
| Conversión WAV y cache invalidation al cambiar/eliminar videos fuente | Verificada con fixtures |
| Verificación exacta de capítulos por cantidad, títulos y tiempos | Verificada con FFprobe real sobre un MKV sintético; además se probó el rollback simulado |
| Persistencia y presentación de fallos por episodio | Verificada con fixture aislado |
| Protección de rutas y symlinks | Verificada con fixtures |
| Índice de confianza OP/ED 0–100 y bandas | Pruebas de alta, media, baja, anomalía, métricas ausentes y revisión agregada |
| Snapshot original e historial de correcciones/retiradas | Migración SQLite y prueba de persistencia manual |
| Cola de revisión y selección de episodios elegibles | Frontend tests/build; validación visual Windows pendiente |
| Inyección masiva secuencial con backup/verificación por MKV | Pruebas de lógica/job; validación real en Windows con MKV representativos pendiente |

Las pruebas se ejecutaron en el entorno Python disponible durante la construcción. La configuración de Windows indicada en `setup_windows.bat` todavía debe validarse en un equipo Windows real.


## Validación añadida para la recuperación de funcionalidades CLI

La rama `feature/recover-cli-workflows` incorpora nuevas pruebas para:

- Los valores predeterminados de `templates/` y `plugins/` bajo la raíz del proyecto, incluida la normalización no destructiva de instalaciones que ya usaban `Templates/`.
- La migración no destructiva desde las carpetas anteriores de AppData y desde el directorio `template/` del CLI.
- La sincronización automática de todos los resultados AnimeThemes que coinciden como en el CLI (y fallback a todos los resultados cuando no hay coincidencia), caché de vídeos por URL y conservación de WAV manuales/manifiestos.
- La validación de WAV legibles antes de iniciar el detector y el orden de preparación: dependencias, sincronización, conversión WAV y análisis.
- El endpoint y esquema GraphQL actuales de AnimeThemes, variables de búsqueda/detalle, mapeo de `animethemes`/`animethemeentries` y manejo de cancelación.
- El reporte de progreso por Opening/Ending y bytes transferidos, barra indeterminada durante consultas/tamaños desconocidos y progreso monotónico entre preparación y análisis.
- La descarga de FFmpeg/FFprobe en segundo plano y la instalación diferida de MKVToolNix al inyectar capítulos.
- La protección del contrato de detección OP/ED (`0.68`, 22.050 Hz, chroma, correlación, filtro ED del 70 %) y las nuevas métricas auxiliares de confianza que no alteran la selección del ganador.
- La extracción segura de ZIP y la recuperación de carpetas de plugins tras una interrupción.
- Los nombres de capítulos históricos y el idioma `spa/es`, manteniendo backup, verificación y rollback.

**Estado de esta rama:** GitHub Actions completó correctamente `compileall`, `pytest` (**94 pruebas aprobadas, 1 warning**), `npm test` y `npm run build`. La suite cubre las bandas de confianza, la migración e historial de correcciones, la protección del motor de detección y la cola de inyección. La instalación real de herramientas, las consultas/descargas externas de AnimeThemes, la conversión con FFmpeg sobre vídeos reales, la inyección masiva sobre MKV representativos y la inspección visual se deben validar además en Windows.


## Comprobaciones pendientes

- La instalación de dependencias del frontend y el build Vite pasaron en CI Linux. Queda pendiente validar el setup completo sobre el equipo Windows de destino.
- No se realizaron solicitudes reales a AnimeThemes ni descargas externas de templates.
- No se ejecutó FFmpeg/FFprobe/MKVToolNix contra archivos multimedia reales.
- La restauración del backup ante fallo se probó con una simulación de los ejecutables, no con una modificación real de MKV.
- No se validó la preservación de todas las pistas, etiquetas y metadatos MKV en un flujo real de inyección.
- No se realizó una prueba visual de la interfaz en Windows ni una prueba de distribución empaquetada.

## Interpretación

La base Python, la persistencia, la API local y los flujos de seguridad cuentan con pruebas automatizadas iniciales. Esto no equivale a certificación de producción. Antes de utilizar la inyección sobre una biblioteca real, deben completarse las validaciones pendientes con copias de MKV representativas y comprobar manualmente el resultado con MKVToolNix.


## Corrección posterior: arranque de Windows

Se añadieron `setup_windows.ps1`, `run_windows.ps1` y `dev_windows.ps1`. Los `.bat` priorizan Windows Terminal + PowerShell 7 (`wt.exe pwsh.exe`), con fallback a una ventana de `pwsh.exe`. El setup no utiliza `where npm` como única comprobación: recompone PATH desde el entorno Process/User/Machine, contempla gestores de versiones comunes y localiza `node.exe` y `npm.cmd` por separado. La condición de versión permite Node.js 20 o superior, incluida la rama 26.

**Pendiente de validación:** el usuario confirmó que el selector de carpetas y la nueva configuración de Node/npm funcionan en su instalación local después de aplicar los cambios de PowerShell. Esta versión del ZIP incorpora esos cambios y vuelve a priorizar Windows Terminal + PowerShell 7. Los nuevos lanzadores `.bat` no pudieron ejecutarse en Windows desde el entorno de construcción.


## Actualización de interfaz y biblioteca

- Se conserva la ruta relativa y los segmentos de carpeta de cada MKV en `GET /api/series/{series_name}`. El frontend muestra el árbol recursivo con los nombres reales de carpeta; no consulta servicios externos para inferir temporadas o cours.
- Se incorporó el endpoint local `POST /api/system/select-folder` y su botón Examinar en Settings.
- El Dashboard mantiene cabecera y tarjetas de resumen fuera del scroll de la tabla; la lista de series tiene desplazamiento interno y cabecera fija.
- Las notificaciones Toast ahora usan `position: fixed` y tipos success/info/warning/error, sin participar en el flex principal ni desplazar el contenido.
- Los lanzadores `.bat` priorizan Windows Terminal + PowerShell 7 (`wt.exe pwsh.exe`).

La prueba visual de estos ajustes debe completarse en el equipo Windows de destino.


## UI scroll-layout refinement

- Folder nodes in Series Detail start collapsed. Applying a search or status filter expands the matching hierarchy; clearing the filter returns the tree to its collapsed default.
- Series Detail uses a centered, viewport-height workspace. Breadcrumb, page header, series summary, episode toolbar, column headings and footer remain outside the independently scrollable folder tree.
- Logs uses the same workspace pattern: heading, metrics, toolbar, column headings and footer remain visible while the log entries scroll independently.
- Dashboard retains its fixed overview and independently scrollable library table. The application-wide page viewport remains the shared centered scroll context for form-oriented views such as Settings and Individual Analysis.
- The frontend Vite production build was not run in this environment because npm dependencies are not installed locally and network installation is unavailable. Rebuild on the target Windows machine with `npm.cmd run build` in `frontend`.

## Workspace scrolling and collapsed folder defaults

- Folder tree nodes now start collapsed at every depth. While a search query or status filter is active, the hierarchy temporarily expands to expose matching episodes; clearing the filter collapses the folders again.
- Series Detail now keeps its breadcrumb, title/actions, series summary, episode toolbar, column headings and footer fixed within the centered application viewport. Only the recursive folder/episode region scrolls vertically.
- Logs now keeps its title/actions, summary metrics, filters, column headings and footer visible while its record list scrolls independently.
- Dashboard retains its fixed overview and scrollable series table. Settings and Individual Analysis continue to use the shared central page viewport, which scrolls independently of the application sidebar and topbar.
- Backend tests at the time of the first workspace-scroll release: 29 passed; current package validation is recorded above.
- Frontend syntax parsing: 16 JavaScript/JSX/TypeScript source files checked.
- CSS parsing: 5 stylesheets checked.
- The Vite production build was not run here because the frontend dependencies are not installed in this environment. Run `npm.cmd run build` from `frontend` on Windows to validate the complete production bundle.


## AniList public artwork search and scroll correction

- Replaced the TMDb-token workflow with the public AniList GraphQL API. Public anime search and metadata lookups do not require OAuth, an API key, a user token or a Settings configuration.
- The Dashboard now provides a per-series artwork action; the series detail view provides the same search action. Both open the shared modal.
- The modal performs one initial query using the local folder name. The user can edit the title and search manually, explicitly select a matching AniList result, or upload a local JPEG/PNG/WebP image.
- AniList returns candidates only. The selected media ID is revalidated before its poster is downloaded. Only the user-selected poster is cached; the application does not bulk search the library or scrape the catalogue.
- Legacy posters from the earlier TMDb build are migrated once into the provider-neutral `series_artwork` table. Their files remain in place during migration, and the old TMDb credential is removed. A migration marker prevents a deleted legacy image from being reimported on later starts.
- Artwork files are size-limited, MIME/signature checked, restricted to approved AniList CDN HTTPS hosts for remote images, and served from the local app cache. The source URL is not exposed as a direct frontend-controlled download target.
- AniList search and download are tested with mocked HTTP responses. No live remote request was made during this build, and no key/token is required for that test.
- The Series Detail fixed area (breadcrumb, title/actions, series summary and progress) is separated from the episode panel. The folder/episode tree has a bounded scroll viewport. The markup and CSS were parsed, but visual verification must be completed in the user's Windows browser.

Historical validation for 1.3.1: 51 backend tests passed; 17 frontend JS/JSX/TS source files were parsed without syntax errors; 5 stylesheets remain syntax-checked. Vite production build, live AniList request, and visual Windows verification remain pending.


## AniList folder-year normalization

- AniList search now removes a trailing four-digit release year enclosed in parentheses, square brackets or curly braces from the query sent to AniList (for example, `Dan Dan Dan (2019)` becomes `Dan Dan Dan`).
- The user's text in the modal and the actual local series/folder name are not modified. The search response exposes both `original_query` and the normalized `query`, plus a `year_suffix_removed` flag.
- Season labels and other non-year parenthetical text are preserved.
- Regression coverage includes year suffix variants, whitespace normalization, titles containing numbers, and an API-call assertion for `Dan Dan Dan (2019)`.


Historical validation for 1.3.2: `python -m compileall -q backend core scripts tests` passed; `python -m pytest -q` passed 51 tests; TypeScript Compiler API syntax parsing passed for all 17 frontend JS/JSX/TS files. The complete Vite production build remains pending because frontend dependencies are not installed in this build environment.


## AniList series metadata, synopsis, poster lightbox and banner (1.5.0)

- A selected AniList media ID is stored in the dedicated `series_anilist_links` table. The user's existing selected AniList cover associations are migrated into this table once; a custom/local poster can coexist with the selected AniList metadata link.
- `GET /api/artwork/series/{series_name}/metadata` fetches the selected entry's current AniList details on demand. `DELETE /api/artwork/series/{series_name}/metadata` removes only that association and does not delete the local poster.
- The API returns a sanitized plain-text synopsis, title variants, year, format, season, country of origin, genres, external episode count/duration, validated cover/banner URLs and AniList source URL. The frontend renders the synopsis as text, provides an expanded poster lightbox, and displays the local folder title separately when it differs from AniList.
- AniList metadata is not persisted as a local catalogue snapshot. It is held in a bounded process-memory cache (15-minute TTL, maximum 40 entries). Only the user's selected AniList ID is stored persistently. This avoids turning OpEdTagger into a catalogue mirror or data-hoarding service.
- The optional banner is displayed at very low opacity in the series hero header only, with a gradient wash. It does not appear behind the folder tree or episode rows. If the banner is unavailable, the hero remains functional.
- Local folder hierarchy and episode counts remain authoritative. AniList external episode counts are clearly labelled as external metadata and are not used to calculate local statistics.
- Tests cover metadata mapping, sanitization of HTML/unsafe script content, short-lived cache behavior, unlinking while retaining local poster files, database migration of existing AniList associations and GET/DELETE metadata API routes.

Current validation for 1.5.0: `python -m compileall -q backend core scripts tests` passed; `PYTHONPATH=. pytest -q` passed 60 tests; TypeScript Compiler API syntax parsing passed for 18 frontend JS/JSX/TS files; CSS parsing passed for 5 stylesheets. The Vite production build and live AniList request were not run in the build environment; visual verification remains pending in the target Windows browser.

## UI refinement: compact Series Detail and theme persistence (1.5.1)

- The Series Detail hero was compacted vertically to give the independently scrollable folder/episode panel more viewport height. The poster was increased slightly while synopsis, chips, action links, stats spacing, and fixed-header gaps were reduced.
- AniList metadata now carries a small explicit source marker. Local processing metrics are exposed with an accessible "Estadísticas de la biblioteca local" label; the local episode count remains separate from AniList's external episode count.
- Series banner opacity and brightness are controlled by theme tokens: the dark theme uses a slightly stronger image treatment, while the light theme has a more visible banner with a surface gradient to preserve legibility.
- Theme changes in Topbar and Settings apply immediately and persist silently. API failures still produce an error notification. The explicit "Guardar preferencias" action excludes the theme, preventing a redundant success Toast for a presentation-only change.
- Validation for this refinement: the existing Python suite remains at 60 passing tests; frontend JavaScript/JSX parsing and CSS parsing were repeated. A Vite production build and visual verification remain pending on the target Windows installation.


## Navegación entre series sin contenido obsoleto (1.5.2)

- Al seleccionar una serie, el estado de detalle anterior se limpia antes de iniciar la nueva solicitud. La interfaz presenta un estado de carga neutro con el nombre de destino, no la portada, banner, sinopsis, estadísticas ni episodios de la selección anterior.
- Los resultados de detalle se etiquetan con el nombre de serie al que pertenecen; la vista solo se monta cuando esa etiqueta coincide con la selección activa.
- Se añadió una secuencia de solicitudes para descartar respuestas fuera de orden. Si el usuario abre Dan Da Dan y después Dr. STONE antes de que termine la primera solicitud, una respuesta tardía de Dan Da Dan no puede sobrescribir la vista de Dr. STONE. La misma protección aplica al reabrir una serie.
- Si la carga falla, se muestra el error de la serie solicitada con acciones para reintentar o volver al Dashboard.
- Se añadió una prueba Node.js con el ejecutor nativo `node:test` para verificar la invalidación de solicitudes anteriores.

La validación de esta iteración se registra en el paquete entregado. El build de Vite puede completarse en Windows con `npm.cmd run build` dentro de `frontend`.


## Navegación entre series sin contenido obsoleto (1.5.2)

- Al seleccionar una serie, el estado del detalle anterior se limpia antes de iniciar la nueva solicitud. La interfaz muestra únicamente un estado de carga neutro hasta recibir los datos de la serie destino.
- Los resultados se etiquetan con el nombre de serie al que pertenecen; el detalle solo se monta cuando esa etiqueta coincide con la selección activa.
- Se añadió un control de secuencia de solicitudes para descartar respuestas fuera de orden. Una respuesta tardía de Dan Da Dan no puede sobrescribir una selección posterior de Dr. STONE, ni una respuesta de una apertura anterior puede reemplazar un reintento más reciente.
- Si la carga falla, se muestra el error de la serie solicitada con acciones para reintentar o volver al Dashboard.
- Pruebas Python: 60 aprobadas. Pruebas de secuencia frontend con Node `node:test`: 3 aprobadas. TypeScript Compiler API: 20 archivos JS/JSX/TS analizados sin errores de sintaxis. CSS: 5 hojas analizadas sin errores.
- El build Vite no se ejecutó en este entorno porque las dependencias npm no están instaladas localmente. Debe validarse en Windows con `npm.cmd run build` dentro de `frontend`.
