# Datos locales de migración

Coloca aquí tu exportación personal `anime_master.legacy.json` antes de ejecutar la migración.

Los archivos de esta carpeta están excluidos de Git porque pueden contener nombres de series, episodios y timestamps de la biblioteca del usuario. No subas datos personales al repositorio.

Comando desde la raíz del proyecto:

```powershell
.venv\Scripts\python.exe -m scripts.migrate_legacy_json migration\local\anime_master.legacy.json
```

Añade `--apply` únicamente después de revisar el informe dry-run.
