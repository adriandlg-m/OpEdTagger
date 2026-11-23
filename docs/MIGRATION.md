# Migración del JSON legacy

El JSON legacy es un archivo personal de cada instalación y no se incluye en Git. Coloca tu copia en `migration/local/anime_master.legacy.json`; `migration/local/` está excluida del repositorio. El proceso no modifica el archivo original.

1. Inicia la aplicación y configura en Settings la biblioteca correcta.
2. Cierra la aplicación para evitar escrituras concurrentes a la base SQLite.
3. Ejecuta `python -m scripts.migrate_legacy_json migration/local/anime_master.legacy.json` para un dry-run.
4. Revisa `eligible_records`, `skipped_records` y `missing_or_invalid`.
5. Crea una copia externa adicional del JSON si se trata de datos importantes.
6. Ejecuta con `--apply` solo cuando el informe sea correcto.
7. Verifica el recuento en SQLite y vuelve a escanear la biblioteca desde Settings.

La migración importa registros legacy con `analysis_status=STALE`, `analyzer_version=legacy-json` y sin template signature/fingerprint moderno. Sus timestamps se conservan como referencia visible, pero no son válidos para la caché, revisión ni inyección del motor nuevo; el siguiente procesamiento los volverá a analizar. Todos quedan `NOT_REQUESTED` en inyección. No se infiere que un MKV ya tenga capítulos.

La importación utiliza el hash SHA-256 del archivo fuente y `migration_history` para evitar repetir el mismo lote. La copia `.pre-migration.bak` y el JSON original se conservan.
