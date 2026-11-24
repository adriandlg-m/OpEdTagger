# Arquitectura y decisiones

## Estilo arquitectónico

Aplicación local modular, desplegada como un único backend FastAPI y un frontend React. Los routers validan y traducen solicitudes; los servicios orquestan casos de uso; `core` contiene lógica reutilizable; `infrastructure` resuelve persistencia, configuración y logging. No se introducen microservicios, Redis ni Celery.

## Responsabilidades

- `frontend`: presentación, navegación y estado de pantalla. La vista de serie navega por niveles (serie → carpeta → episodio), manteniendo carpetas no seleccionadas contraídas; la revisión de OP/ED se presenta en un espacio de episodio dedicado. No contiene lógica de análisis ni de escritura MKV.
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

`NOT_REQUESTED` → `IN_PROGRESS` → `SUCCEEDED` o `FAILED`. Si el proceso se interrumpe durante la fase de escritura, startup conserva la transacción en `backups/recovery/` y cambia el valor a `RECOVERY_REQUIRED`. Las interrupciones en `PREPARING` o `BACKUP_READY` se limpian porque todavía no hubo modificación y el episodio vuelve a `NOT_REQUESTED`.

Los estados de un job son `queued`, `running`, `completed`, `completed_with_errors`, `failed`, `cancelled` o `interrupted`. Los trabajos `series` e `injection` comparten un índice único de SQLite que impide más de una operación activa en la misma serie.

## Flujos

1. **Biblioteca:** escaneo recursivo dentro de cada carpeta de serie, filtrando rutas resueltas que salgan del directorio autorizado; reconcilia las filas con el contenido actual y elimina únicamente registros SQLite de archivos/carpetas que ya no existen. Los MKV nunca se eliminan.
2. **Preparación de herramientas:** en Windows x64, el backend inicia la instalación de FFmpeg/FFprobe en un hilo de segundo plano para no bloquear la interfaz. El primer análisis espera a que ambos ejecutables estén verificados. MKVToolNix se descarga de forma diferida antes de la primera inyección. Los paquetes oficiales se validan por SHA-256, se extraen en staging con controles de ruta, se comprueban y se activan atómicamente. Un fallo queda reflejado en `/api/system/health`.
3. **Serie:** un job sincroniza AnimeThemes mediante el GraphQL actual (`graphql.animethemes.moe`), consulta `search(search:, first:)` y `anime(slug:)`, y selecciona todos los resultados cuyo slug/título romaji coincida, con fallback a la lista del proveedor como en el CLI. Cada descarga reporta estado, Opening/Ending y bytes transferidos. La preparación de templates ocupa el primer tramo del progreso; el análisis de episodios continúa del 20 al 100 %. Los WAV se validan antes de consultar caché y analizar. Una interrupción de AnimeThemes se informa y, cuando hay una caché local existente, esta puede reutilizarse.
4. **Análisis individual:** carga multipart temporal, job independiente, resultado guardado en el item del job. No se añade al catálogo ni habilita inyección en esta versión.
5. **Confianza y revisión:** cada OP/ED recibe dos índices heurísticos independientes de 0–100. La confianza de coincidencia evalúa la similitud normalizada y la separación frente a otros templates. La precisión temporal estimada evalúa la anchura del pico de alineación al 95 % y su distinción frente a otras posiciones. La consistencia entre episodios del mismo template y las anomalías se conservan como contexto/advertencias; no reducen artificialmente las dos puntuaciones. La precisión temporal describe la localización del inicio: el final se deriva de la duración del template y no se valida como un segundo límite independiente. Ningún índice es una probabilidad calibrada. La revisión se activa si cualquiera de las dos dimensiones es baja o no evaluable; valores medios recomiendan revisión. La revisión manual permite ajustar o retirar un segmento, conserva los valores originales y añade una entrada a `analysis_segment_history`. Cambiar timestamps invalida el estado de inyección anterior.
6. **Inyección individual o masiva:** los estados `AUTO_APPROVED`, `REVIEW_RECOMMENDED`, `MANUALLY_REVIEWED` y `APPROVED` son elegibles; `PENDING`/`UNASSESSED` no lo son. El preflight informa tamaño total, archivo más grande, espacio requerido/disponible y archivos que ya tienen capítulos. El job durable procesa secuencialmente: solo necesita espacio para el MKV más grande del lote más una reserva, no para la suma de todos. Cada episodio se protege con una copia MKV temporal y, si corresponde, una exportación XML de sus capítulos previos; FFprobe verifica la escritura antes de registrar el éxito en SQLite. La copia completa se elimina después del commit. Los fallos se registran por episodio y no cancelan el resto del lote, salvo que la restauración falle y requiera detener la operación.
7. **Recovery:** jobs `queued/running` pasan a `interrupted`. Las transacciones `PREPARING`/`BACKUP_READY` se eliminan y el episodio vuelve a `NOT_REQUESTED`. Las transacciones `INJECTING` o no reconciliables se conservan en `backups/recovery/` y el episodio pasa a `RECOVERY_REQUIRED`. Las transacciones `VERIFIED` cuyo estado SQLite es `SUCCEEDED` se finalizan y limpian. No hay repetición destructiva automática.

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
- Respaldo MKV temporal obligatorio, comprobación de espacio antes del lote y de nuevo antes de cada copia.
- Restauración directa del original sin un segundo archivo temporal en la unidad de medios; el respaldo permanece intacto hasta validar la restauración.
- XML ligero de los capítulos previos cuando se reemplazan.
- Limpieza del MKV completo solo tras el commit SQLite; los casos no recuperables conservan la transacción en `backups/recovery/`.

## Decisiones abiertas

- Selección nativa de carpetas/archivos para una futura shell de escritorio, evitando carga multipart de archivos gigantes.
- Reanudar fases no destructivas de jobs interrumpidos.
- SSE si el polling deja de ser suficiente.
- Integración individual-analysis → revisión/inyección.
- Ampliar las pruebas de instalación automática en Windows real, incluyendo interrupciones de red, extracción de archivos, actualizaciones y recuperación de instalaciones parciales.
- Empaquetado con backend, frontend compilado y herramientas multimedia, sujeto a licencias y validación Windows.


## User-defined folder hierarchy

The library scanner preserves folder structure relative to each series directory. Episode API records expose `relative_path`, `folder_path`, and `folder_segments` using forward slashes for cross-platform consistency. The frontend uses these fields for a drill-down navigator: the series screen lists only top-level folders, entering a folder shows its direct episodes and collapsed child folders, and selecting an episode opens a dedicated review workspace. Search/status filters apply across the series while folder navigation retains the current filter, batch selection and folder scroll positions. The app does not label directories as official seasons or cours and does not use external metadata providers to reorganize local files.
