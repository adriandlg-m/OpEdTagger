# Arquitectura y decisiones

## Estilo arquitectónico

Aplicación local modular, desplegada como un único backend FastAPI y un frontend React. Los routers validan y traducen solicitudes; los servicios orquestan casos de uso; `core` contiene lógica reutilizable; `infrastructure` resuelve persistencia, configuración y logging. No se introducen microservicios, Redis ni Celery.

## Responsabilidades

- `frontend`: presentación, navegación y estado de pantalla. No contiene lógica de análisis ni de escritura MKV.
- `backend/api/routers`: HTTP, validación y traducción de errores.
- `backend/services`: casos de uso de biblioteca, análisis, jobs, templates, preparación de herramientas y logs.
- `backend/infrastructure`: rutas de datos, SQLite, logging y configuración.
- `core`: algoritmo de detección, fingerprints y operaciones multimedia.
- `migration`: snapshot del JSON legacy; nunca se modifica automáticamente.

## Persistencia

SQLite usa WAL, foreign keys y busy timeout. Tablas:

- `series`, `episodes`: catálogo local indexado.
- `analysis_results`: resultados OP/ED actuales, snapshots originales, versión del modelo de confianza y estados separados.
- `analysis_segment_history`: correcciones, retiradas y aprobaciones por OP/ED, con valores anteriores y nuevos.
- `jobs`, `job_items`: ejecución persistente e historial.
- `activity_logs`: logs estructurados.
- `app_settings`: preferencias de UI.
- `migration_history`: idempotencia de importación legacy.
- `schema_migrations`: migraciones explícitas de versión 1 a 5 (confianza, snapshots originales, historial y exclusión mutua de trabajos por serie).

Los resultados asociados a archivos de biblioteca se vuelven a validar por versión del algoritmo, versión del modelo de confianza, firma de templates, tamaño, mtime y fingerprint muestreado del archivo. La versión nueva de confianza invalida los resultados cacheados anteriores para que los episodios se vuelvan a evaluar una vez y reciban métricas normalizadas. El score bruto del detector y la selección de su candidato ganador permanecen intactos. Los WAV derivados tienen un manifiesto que los invalida cuando cambia o desaparece el video fuente. El fingerprint es una optimización de caché, no un checksum forense de todos los bytes.

## Estados

### Análisis

`PENDING` → `COMPLETED` | `NO_MATCH` | `FAILED`. Los registros importados desde el JSON antiguo se marcan `STALE`: se conservan para consulta, pero no son válidos para la caché ni para aprobación/inyección hasta repetir el análisis con el motor actual.

### Revisión

`NOT_REQUIRED` para `NO_MATCH`; `AUTO_APPROVED` para detecciones de confianza alta; `REVIEW_RECOMMENDED` para confianza media; `PENDING` para baja confianza o sin evaluar; `MANUALLY_REVIEWED` cuando se verifican manualmente los segmentos; `APPROVED` cuando el usuario confirma explícitamente todos los resultados del episodio. Los estados de revisión se agregan desde la confianza independiente de cada OP y ED.

### Inyección

`NOT_REQUESTED` → `IN_PROGRESS` → `SUCCEEDED` o `FAILED`. Si el proceso se interrumpe mientras el valor está en `IN_PROGRESS`, startup cambia a `RECOVERY_REQUIRED`. Ese estado bloquea nuevas inyecciones automáticas y exige inspección humana del MKV y del respaldo.

Los estados de un job son `queued`, `running`, `completed`, `completed_with_errors`, `failed`, `cancelled` o `interrupted`. Los trabajos `series` e `injection` comparten un índice único de SQLite que impide más de una operación activa en la misma serie.

## Flujos

1. **Biblioteca:** escaneo recursivo dentro de cada carpeta de serie, filtrando rutas resueltas que salgan del directorio autorizado; reconcilia las filas con el contenido actual y elimina únicamente registros SQLite de archivos/carpetas que ya no existen. Los MKV nunca se eliminan.
2. **Preparación de herramientas:** en Windows x64, el backend inicia la instalación de FFmpeg/FFprobe en un hilo de segundo plano para no bloquear la interfaz. El primer análisis espera a que ambos ejecutables estén verificados. MKVToolNix se descarga de forma diferida antes de la primera inyección. Los paquetes oficiales se validan por SHA-256, se extraen en staging con controles de ruta, se comprueban y se activan atómicamente. Un fallo queda reflejado en `/api/system/health`.
3. **Serie:** un job sincroniza AnimeThemes mediante el GraphQL actual (`graphql.animethemes.moe`), consulta `search(search:, first:)` y `anime(slug:)`, y selecciona todos los resultados cuyo slug/título romaji coincida, con fallback a la lista del proveedor como en el CLI. Cada descarga reporta estado, Opening/Ending y bytes transferidos. La preparación de templates ocupa el primer tramo del progreso; el análisis de episodios continúa del 20 al 100 %. Los WAV se validan antes de consultar caché y analizar. Una interrupción de AnimeThemes se informa y, cuando hay una caché local existente, esta puede reutilizarse.
4. **Análisis individual:** carga multipart temporal, job independiente, resultado guardado en el item del job. No se añade al catálogo ni habilita inyección en esta versión.
5. **Confianza y revisión:** cada OP/ED recibe un índice heurístico 0–100 a partir de similitud normalizada, claridad del pico, margen frente a otros templates y consistencia temporal del mismo template entre episodios. Solo se evalúa consistencia cruzada con al menos tres detecciones comparables. Se almacena el modelo/versionado y las razones. El índice no es una probabilidad calibrada. La revisión manual permite ajustar o retirar un segmento; conserva los valores originales y añade una entrada a `analysis_segment_history`. Cambiar timestamps invalida el estado de inyección anterior.
6. **Inyección individual o masiva:** los estados `AUTO_APPROVED`, `REVIEW_RECOMMENDED`, `MANUALLY_REVIEWED` y `APPROVED` son elegibles; `PENDING`/`UNASSESSED` no lo son. El usuario selecciona episodios y confirma una operación masiva. Un job durable procesa los MKV secuencialmente, con backup por archivo, detección de capítulos existentes, escritura con MKVToolNix y verificación posterior de cantidad, títulos y tiempos con FFprobe. Los fallos se registran por episodio y no cancelan el resto del lote.
7. **Recovery:** jobs `queued/running` pasan a `interrupted`. Inyecciones `IN_PROGRESS` pasan a `RECOVERY_REQUIRED`. No hay repetición destructiva automática.

## Seguridad

- Backend enlazado a `127.0.0.1`.
- Nombres de serie y rutas de templates validados.
- Rutas de episodios verificadas dentro de la biblioteca actual.
- Rutas resueltas para impedir symlinks que escapen de la biblioteca.
- Descargas de media limitadas a HTTPS.
- Descargas de herramientas verificadas mediante SHA-256, extracción ZIP/7z con validación de rutas y activación atómica tras pruebas de versión.
- Carpetas de plantillas y herramientas en la raíz del proyecto, excluidas de Git; los datos de ejecución siguen en AppData.
- Las plantillas nuevas se descargan a staging; al iniciar una descarga posterior se recupera una carpeta anterior si el proceso se interrumpió durante el intercambio.
- Archivos MKV nunca se modifican durante análisis.
- Respaldo previo y rollback best-effort durante inyección.
- Si la restauración automática falla, la respuesta entrega ubicación del backup y el estado requiere recuperación manual.

## Decisiones abiertas

- Selección nativa de carpetas/archivos para una futura shell de escritorio, evitando carga multipart de archivos gigantes.
- Reanudar fases no destructivas de jobs interrumpidos.
- SSE si el polling deja de ser suficiente.
- Integración individual-analysis → revisión/inyección.
- Ampliar las pruebas de instalación automática en Windows real, incluyendo interrupciones de red, extracción de archivos, actualizaciones y recuperación de instalaciones parciales.
- Empaquetado con backend, frontend compilado y herramientas multimedia, sujeto a licencias y validación Windows.


## User-defined folder hierarchy

The library scanner preserves folder structure relative to each series directory. Episode API records expose `relative_path`, `folder_path`, and `folder_segments` using forward slashes for cross-platform consistency. The frontend builds a recursively expandable tree from these fields. The app does not label directories as official seasons or cours and does not use external metadata providers to reorganize local files.
