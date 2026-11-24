import { useEffect, useState } from 'react';
import { Check, Database, ExternalLink, Folder, FolderOpen, HardDrive, Info, Moon, RefreshCw, ShieldCheck, SlidersHorizontal, Sun } from 'lucide-react';
import PageHeader from '../components/PageHeader.jsx';
import StatusBadge from '../components/StatusBadge.jsx';
import { api } from '../api/client.js';
import '../styles/settings.css';

export default function Settings({ theme, onThemeChange, settings, preferences, onSaveSettings, onSavePreferences, onToast, onScanLibrary }) {
  const [path, setPath] = useState(settings?.library_path || '');
  const [templatePath, setTemplatePath] = useState(settings?.template_path || '');
  const [pluginsPath, setPluginsPath] = useState(settings?.plugins_path || '');
  const [backupPath, setBackupPath] = useState(settings?.backup_path || '');
  const [confirmInjection, setConfirmInjection] = useState(preferences?.confirm_injection !== false);
  const [showOutliers, setShowOutliers] = useState(preferences?.show_outliers !== false);
  const [logRefresh, setLogRefresh] = useState(preferences?.log_refresh !== false);
  const [tools, setTools] = useState(null);
  const [pluginManager, setPluginManager] = useState(null);
  const [saving, setSaving] = useState(false);
  const [browsingLibrary, setBrowsingLibrary] = useState(false);

  useEffect(() => {
    setPath(settings?.library_path || '');
    setTemplatePath(settings?.template_path || '');
    setPluginsPath(settings?.plugins_path || '');
    setBackupPath(settings?.backup_path || '');
  }, [settings]);
  useEffect(() => {
    setConfirmInjection(preferences?.confirm_injection !== false);
    setShowOutliers(preferences?.show_outliers !== false);
    setLogRefresh(preferences?.log_refresh !== false);
  }, [preferences]);
  useEffect(() => {
    let mounted = true;
    api.health().then((data) => {
      if (!mounted) return;
      setTools(data.tools || null);
      setPluginManager(data.plugin_manager || null);
    }).catch(() => {
      if (mounted) { setTools(null); setPluginManager(null); }
    });
    return () => { mounted = false; };
  }, []);

  const browseLibrary = async () => {
    setBrowsingLibrary(true);
    try {
      const result = await api.selectFolder(path, 'Seleccionar biblioteca de animes');
      if (result?.selected && result.path) {
        setPath(result.path);
        onToast('Carpeta seleccionada. Pulsa «Guardar rutas» para confirmar.', 'info');
      }
    } catch (error) {
      onToast(error.message || 'No se pudo abrir el selector de carpetas.', 'error');
    } finally {
      setBrowsingLibrary(false);
    }
  };

  const saveLibrary = async () => {
    if (!path.trim()) { onToast('Introduce la ruta completa de la biblioteca.', 'warning'); return; }
    setSaving(true);
    try {
      await onSaveSettings({ library_path: path.trim(), template_path: templatePath.trim(), plugins_path: pluginsPath.trim(), backup_path: backupPath.trim() });
    } catch (error) { onToast(error.message, 'error'); }
    finally { setSaving(false); }
  };

  const savePrefs = async () => {
    // Theme changes are persisted immediately and silently by App. This button
    // only saves the remaining preferences, so a theme toggle never generates
    // a redundant "Preferences saved" toast.
    try { await onSavePreferences({ confirm_injection: confirmInjection, show_outliers: showOutliers, log_refresh: logRefresh }); }
    catch (error) { onToast(error.message, 'error'); }
  };

  const toolStatus = (name) => tools?.[name]?.available ? 'Listo' : 'Pendiente';

  return (
    <div className="page-content secondary-page settings-page">
      <PageHeader title="Settings" description="Configura la biblioteca local y las herramientas multimedia de OpEdTagger." eyebrow="PREFERENCIAS / CONFIGURACIÓN" />
      <div className="settings-layout">
        <section className="panel settings-card settings-library-card">
          <div className="settings-card-heading"><div className="settings-icon settings-icon-violet"><FolderOpen size={19} /></div><div><h2>Rutas de la aplicación</h2><p>Estas rutas pertenecen al equipo donde se ejecuta el backend local.</p></div></div>
          <div className="settings-section">
            <label className="settings-label" htmlFor="library-path">Biblioteca de animes</label>
            <div className="settings-path-control">
              <div className="settings-path-input-wrap"><Folder size={16} /><input id="library-path" type="text" value={path} onChange={(e) => setPath(e.target.value)} placeholder="D:\\Anime" spellCheck={false} /></div>
              <button type="button" className="button button-secondary settings-browse-button" onClick={browseLibrary} disabled={browsingLibrary || saving}>
                <FolderOpen size={15} /> {browsingLibrary ? 'Abriendo…' : 'Examinar'}
              </button>
            </div>
            <div className="settings-field-hint"><Info size={13} /><span>Selecciona la carpeta raíz que contiene las carpetas de tus series. El selector abre el explorador nativo de Windows.</span></div>
          </div>
          <div className="settings-section">
            <label className="settings-label" htmlFor="template-path">Directorio de templates</label>
            <div className="settings-path-control"><div className="settings-path-input-wrap"><Folder size={16} /><input id="template-path" type="text" value={templatePath} onChange={(e) => setTemplatePath(e.target.value)} spellCheck={false} /></div></div>
          </div>
          <div className="settings-section">
            <label className="settings-label" htmlFor="plugins-path">Herramientas multimedia</label>
            <div className="settings-path-control"><div className="settings-path-input-wrap"><Folder size={16} /><input id="plugins-path" type="text" value={pluginsPath} onChange={(e) => setPluginsPath(e.target.value)} spellCheck={false} /></div></div>
            <div className="settings-field-hint"><Info size={13} /><span>En Windows x64, OpEdTagger prepara automáticamente FFmpeg y MKVToolNix al iniciar. Las herramientas se guardan aquí; si una descarga falla, el estado del gestor aparecerá en Integraciones.</span></div>
          </div>
          <div className="settings-section">
            <label className="settings-label" htmlFor="backup-path">Copias de seguridad de MKV</label>
            <div className="settings-path-control"><div className="settings-path-input-wrap"><Folder size={16} /><input id="backup-path" type="text" value={backupPath} onChange={(e) => setBackupPath(e.target.value)} spellCheck={false} /></div></div>
          </div>
          <div className="settings-card-footer"><span>La ruta de biblioteca debe existir antes de guardarse.</span><button type="button" className="button button-primary settings-save-button" onClick={saveLibrary} disabled={saving}><Check size={15} /> {saving ? 'Guardando…' : 'Guardar rutas'}</button></div>
          <div className="settings-card-footer"><span>Vuelve a leer las carpetas y sus archivos MKV.</span><button type="button" className="button button-secondary settings-save-button" onClick={onScanLibrary}><RefreshCw size={15} /> Analizar biblioteca</button></div>
        </section>

        <div className="settings-right-column">
          <section className="panel settings-card settings-integrations-card">
            <div className="settings-card-heading"><div className="settings-icon settings-icon-blue"><Database size={19} /></div><div><h2>Integraciones y herramientas</h2><p>Disponibilidad detectada en el equipo.</p></div></div>
            <div className="settings-integration-list">
              <article className="settings-integration"><div className="settings-integration-logo anime-logo">AT</div><div className="settings-integration-copy"><strong>AnimeThemes</strong><span>La conectividad se comprueba cuando se realiza una búsqueda o descarga de templates.</span></div><StatusBadge status="En línea bajo demanda" compact /></article>
              <article className="settings-integration"><div className="settings-integration-logo tool-logo"><HardDrive size={18} /></div><div className="settings-integration-copy"><strong>Preparación automática</strong><span>{pluginManager?.message || 'Comprobando el estado del gestor de herramientas…'}{pluginManager?.last_error ? ` ${pluginManager.last_error}` : ''}</span></div><StatusBadge status={pluginManager?.status === 'ready' ? 'Listo' : pluginManager?.status === 'error' ? 'Error' : pluginManager?.status === 'manual_setup_required' ? 'Manual' : 'Pendiente'} compact /></article>
              {['ffmpeg', 'ffprobe', 'mkvpropedit', 'mkvmerge'].map((name) => <article className="settings-integration" key={name}><div className="settings-integration-logo tool-logo"><HardDrive size={18} /></div><div className="settings-integration-copy"><strong>{name}</strong><span>{tools?.[name]?.path || (tools ? 'No se encontró en plugins_path ni PATH.' : 'Comprobando…')}</span></div><StatusBadge status={toolStatus(name)} compact /></article>)}
            </div>

            <section className="artwork-provider-info">
              <div className="artwork-provider-mark">AL</div>
              <div className="artwork-provider-copy">
                <strong>Carátulas de anime mediante AniList</strong>
                <span>La búsqueda utiliza el catálogo público de AniList. No requiere cuenta, API Key ni token.</span>
                <a href="https://anilist.co" target="_blank" rel="noreferrer">Visitar AniList <ExternalLink size={12} /></a>
              </div>
              <StatusBadge status="Disponible" compact />
            </section>
          </section>

          <section className="panel settings-card settings-preferences-card">
            <div className="settings-card-heading"><div className="settings-icon settings-icon-violet"><SlidersHorizontal size={19} /></div><div><h2>Preferencias</h2><p>Preferencias persistidas localmente.</p></div></div>
            <div className="settings-preference-list">
              <div className="settings-preference-row"><div className="settings-preference-copy"><strong>Apariencia</strong><span>Selecciona el tema visual.</span></div><div className="settings-theme-selector" role="group"><button type="button" className={theme === 'mate' ? 'active' : ''} onClick={() => onThemeChange('mate')}><Moon size={13} /> Mate</button><button type="button" className={theme === 'light' ? 'active' : ''} onClick={() => onThemeChange('light')}><Sun size={13} /> Claro</button></div></div>
              <div className="settings-preference-row"><div className="settings-preference-copy"><strong>Confirmar antes de inyectar</strong><span>La confirmación es obligatoria por seguridad.</span></div><button type="button" role="switch" className="settings-toggle settings-toggle-on" aria-checked="true" aria-label="Confirmación obligatoria" disabled><span /></button></div>
              <div className="settings-preference-row"><div className="settings-preference-copy"><strong>Resaltar alertas de Outliers</strong><span>Destacar visualmente anomalías en el detalle de serie.</span></div><button type="button" role="switch" className={`settings-toggle ${showOutliers ? 'settings-toggle-on' : ''}`} aria-checked={showOutliers} onClick={() => setShowOutliers((value) => !value)}><span /></button></div>
              <div className="settings-preference-row"><div className="settings-preference-copy"><strong>Actualización de Logs</strong><span>Actualizar automáticamente mientras se visualizan.</span></div><button type="button" role="switch" className={`settings-toggle ${logRefresh ? 'settings-toggle-on' : ''}`} aria-checked={logRefresh} onClick={() => setLogRefresh((value) => !value)}><span /></button></div>
            </div>
            <div className="settings-card-footer"><span>Se guardan en la base local de OpEdTagger.</span><button type="button" className="button button-primary settings-save-button" onClick={savePrefs}><Check size={15} /> Guardar preferencias</button></div>
          </section>

          <div className="settings-security-note"><div className="settings-security-icon"><ShieldCheck size={17} /></div><div><strong>Protección de archivos</strong><span>El análisis no modifica MKV. La inyección requiere revisión aprobada, confirmación explícita y copia de seguridad.</span></div></div>
        </div>
      </div>
    </div>
  );
}
