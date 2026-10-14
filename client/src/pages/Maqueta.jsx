import React, { useEffect, useMemo, useRef, useState } from "react";
import { api, fileUrl } from "../api.js";
import { useApp } from "../context.js";
import { Bar, Busy, ErrorBox, Icon, ICONS, Modal, Rel, useBusy, useLoad, useStored } from "../components/ui.jsx";
import Viewer3D from "../components/Viewer3D.jsx";
import { StatusChip, stepLabel } from "./Biblioteca.jsx";
import { bytes, num } from "../format.js";

const PART_ORDER = ["terrain", "buildings", "water", "roads", "green", "frame"];

export default function Maqueta({ param }) {
  const { t, lang, version, notify, confirm, changed } = useApp();
  const { data: m, error, reload } = useLoad(() => api.call("model_get", { model_id: param }), [param, version]);
  const [jobLog, setJobLog] = useState(null);
  const [hidden, setHidden] = useState(() => new Set());
  const [explode, setExplode] = useState(0);
  const [view, setView] = useState("iso");
  const [busy, run] = useBusy();
  const [exportOpen, setExportOpen] = useState(false);
  const [renameOpen, setRenameOpen] = useState(false);
  const [showLog, setShowLog] = useStored("gulliver.showLog", false);

  const active = m && (m.status === "building" || m.status === "queued");
  useEffect(() => {
    if (!active) return undefined;
    const timer = setInterval(async () => {
      if (document.hidden) return;
      const fresh = await reload();
      if (fresh?.job?.id) {
        try { setJobLog(await api.call("job_get", { job_id: fresh.job.id })); } catch { /* the job may just have ended */ }
      }
      if (fresh && fresh.status !== "building" && fresh.status !== "queued") changed();
    }, 1000);
    return () => clearInterval(timer);
  }, [active, reload, changed]);

  const report = m?.report;
  const build = m?.build?.id;
  const glb = report && build ? fileUrl(m.id, "preview.glb", { build }) : null;
  const parts = useMemo(() => PART_ORDER.filter((p) => report?.parts?.[p]), [report]);

  if (error) return <div className="p-6"><ErrorBox error={error} onRetry={reload} /></div>;
  if (!m) return <div className="p-6"><span className="spinner" /> {t("loading")}</div>;

  const toggle = (p) => setHidden((h) => { const n = new Set(h); if (n.has(p)) n.delete(p); else n.add(p); return n; });
  const rebuild = () => run("build", async () => { await api.call("model_build", { model_id: m.id }); notify(t("rebuilding")); await reload(); });
  const cancel = () => run("cancel", async () => { await api.call("job_cancel", { job_id: m.job.id }); await reload(); });
  const duplicate = () => run("dup", async () => { const r = await api.call("model_duplicate", { model_id: m.id }); window.location.hash = `#/nueva?edit=${r.id}`; });
  const vulcan = () => run("vulcan", async () => {
    const r = await api.call("model_send_to_vulcan", { model_id: m.id, tiles: (report?.tiles || []).length > 0 });
    if (r.ok) notify(t("sent_vulcan")); else notify(r.sent?.[0]?.error || t("vulcan_failed"), "error");
  });
  const remove = async () => {
    if (!(await confirm({ title: t("delete_title", { name: m.name }), message: t("delete_msg") }))) return;
    await run("del", async () => { await api.call("model_delete", { model_id: m.id, confirm: true }); window.location.hash = "#/"; changed(); });
  };

  const job = m.job;
  return (
    <div className="model-layout">
      <div className="model-view">
        {glb ? (
          <Viewer3D url={glb} hidden={hidden} explode={explode} view={view}
            labels={{ viewer: t("viewer_label", { name: m.name }), loading: t("loading_3d"), error: t("viewer_error") }} />
        ) : (
          <div className="viewer viewer-empty">
            {active ? <><span className="spinner" /> {t("building")}</> : <span className="help">{m.status === "failed" ? t("build_failed") : t("not_built")}</span>}
          </div>
        )}
        {glb && (
          <div className="viewer-tools">
            <div className="seg" role="group" aria-label={t("camera")}>
              {["iso", "top", "front"].map((v) => <button key={v} type="button" aria-pressed={view === v} onClick={() => setView(v)}>{t(`view_${v}`)}</button>)}
            </div>
            <div className="part-toggles" role="group" aria-label={t("parts")}>
              {parts.map((p) => (
                <button key={p} type="button" className="part-toggle" aria-pressed={!hidden.has(p)} onClick={() => toggle(p)}>
                  <span className="swatch" style={{ background: report.parts[p].color }} />{report.parts[p].label}
                </button>
              ))}
            </div>
            {(report?.tiles || []).length > 1 && (
              <label className="explode">
                <span className="help">{t("explode")}</span>
                <input type="range" min="0" max="1" step="0.05" value={explode} onChange={(e) => setExplode(Number(e.target.value))} />
              </label>
            )}
          </div>
        )}
      </div>

      <div className="model-side space-y-4">
        <div className="space-y-1">
          <div className="flex items-start gap-2">
            <a className="btn btn-sm btn-icon" href="#/" aria-label={t("back")}><Icon d={ICONS.arrowLeft} size={14} /></a>
            <h1 className="min-w-0 flex-1" style={{ overflowWrap: "anywhere" }}>{m.name}</h1>
            <StatusChip status={m.status} t={t} />
          </div>
          {m.place && <div className="help">{m.place}</div>}
          <div className="help">{t("updated")} <Rel ts={m.updated} /></div>
        </div>

        {active && job && (
          <div className="panel space-y-2" aria-live="polite">
            <div className="flex items-center gap-2"><b className="flex-1">{job.state === "queued" ? t("queued") : t("building")}</b>
              <span className="help num">{Math.round((job.progress || 0) * 100)} %</span></div>
            <Bar value={job.progress || 0} alarm={false} />
            <div className="help">{stepLabel(t, job.step)}</div>
            <div className="flex gap-2">
              <Busy busy={busy.cancel} className="btn btn-sm" onClick={cancel}><Icon d={ICONS.stop} size={12} /> {t("cancel")}</Busy>
              <button type="button" className="btn btn-sm" onClick={() => setShowLog(!showLog)}>{showLog ? t("hide_log") : t("show_log")}</button>
            </div>
            {showLog && jobLog?.log && <LogBox lines={jobLog.log} />}
          </div>
        )}
        {m.status === "failed" && (
          <div className="banner banner-danger" role="alert">
            <b>{t("build_failed")}</b>: {m.error}
            <div className="mt-2"><Busy busy={busy.build} className="btn btn-sm" onClick={rebuild}><Icon d={ICONS.refresh} size={12} /> {t("retry")}</Busy></div>
          </div>
        )}

        {report && (
          <>
            <div className="panel facts-grid">
              <div><span>{t("scale")}</span><b>{report.scale}</b></div>
              <div><span>{t("model_size")}</span><b>{report.size_mm.map((v) => num(v, 0, lang)).join(" × ")} <small>mm</small></b></div>
              <div><span>{t("weight")}</span><b>≈ {num(report.weight_g_pla, 0, lang)} <small>g PLA</small></b></div>
              <div><span>{t("volume")}</span><b>{num(report.volume_cm3, 0, lang)} <small>cm³</small></b></div>
              <div><span>{t("relief")}</span><b>{num(report.relief_m[0], 0, lang)}–{num(report.relief_m[1], 0, lang)} <small>m</small></b></div>
              <div><span>{t("buildings")}</span><b>{num(report.buildings, 0, lang)}</b></div>
              <div><span>{t("triangles")}</span><b>{num(report.triangles / 1000, 0, lang)} <small>k</small></b></div>
              <div><span>{t("check")}</span><b className={report.check?.watertight ? "ok-text" : "warn-text"}>{report.check?.watertight ? t("watertight") : t("open_edges", { n: report.check?.open_edges })}</b></div>
            </div>
            {!report.fits_bed && !(report.tiles || []).length && <div className="banner banner-warn">{t("too_big_for_bed", { x: report.bed_mm[0], y: report.bed_mm[1] })}</div>}
            {(report.tiles || []).some((x) => !x.fits_bed) && <div className="banner banner-warn">{t("tiles_too_big", { x: report.bed_mm[0], y: report.bed_mm[1] })}</div>}

            <section className="space-y-2">
              <h2>{t("downloads")}</h2>
              <div className="dl-list">
                <a className="dl" href={fileUrl(m.id, report.files["3mf"], { download: true })}><Icon d={ICONS.download} size={15} /><span><b>3MF</b> {t("dl_3mf")}</span><small>{bytes(report.bytes?.["3mf"], lang)}</small></a>
                <a className="dl" href={fileUrl(m.id, report.files.stl, { download: true })}><Icon d={ICONS.download} size={15} /><span><b>STL</b> {t("dl_stl")}</span><small>{bytes(report.bytes?.stl, lang)}</small></a>
                {report.files.tiles_zip && <a className="dl" href={fileUrl(m.id, report.files.tiles_zip, { download: true })}><Icon d={ICONS.download} size={15} /><span><b>ZIP</b> {t("dl_tiles", { n: report.tiles.length })}</span><small>{bytes(report.bytes?.tiles_zip, lang)}</small></a>}
              </div>
              {(report.tiles || []).length > 0 && (
                <details className="tiles-list">
                  <summary>{t("tiles_detail", { n: report.tiles.length })}</summary>
                  <table className="tbl">
                    <thead><tr><th>{t("tile")}</th><th className="r">mm</th><th>{t("bed")}</th><th>{t("files")}</th></tr></thead>
                    <tbody>
                      {report.tiles.map((tile) => (
                        <tr key={tile.id}>
                          <td><b>{tile.id}</b></td>
                          <td className="r num">{tile.size_mm.slice(0, 2).map((v) => num(v, 0, lang)).join("×")}</td>
                          <td>{tile.fits_bed ? <span className="chip chip-ok">{t("fits")}</span> : <span className="chip chip-danger">{t("does_not_fit")}</span>}</td>
                          <td className="whitespace-nowrap"><a href={fileUrl(m.id, tile.stl, { download: true })}>STL</a> · <a href={fileUrl(m.id, tile["3mf"], { download: true })}>3MF</a></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {report.pins > 0 && <p className="help mt-2">{t("pins_note", { n: report.pins, d: num(m.params.pin_diameter_mm, 1, lang), l: num(2 * m.params.pin_depth_mm - 0.5, 1, lang) })}</p>}
                </details>
              )}
            </section>
          </>
        )}

        <section className="space-y-2">
          <h2>{t("actions")}</h2>
          <div className="flex flex-wrap gap-2">
            <a className="btn" href={`#/nueva?edit=${m.id}`}><Icon d={ICONS.edit} size={14} /> {t("edit_params")}</a>
            <Busy busy={busy.build} onClick={rebuild} disabled={active}><Icon d={ICONS.refresh} size={14} /> {t("rebuild")}</Busy>
            {report && <Busy busy={busy.vulcan} onClick={vulcan}><Icon d={ICONS.send} size={14} /> {t("to_vulcan")}</Busy>}
            {report && <button type="button" className="btn" onClick={() => setExportOpen(true)}><Icon d={ICONS.folder} size={14} /> {t("export")}</button>}
            <Busy busy={busy.dup} onClick={duplicate}><Icon d={ICONS.copy} size={14} /> {t("duplicate")}</Busy>
            <button type="button" className="btn" onClick={() => setRenameOpen(true)}><Icon d={ICONS.edit} size={14} /> {t("rename")}</button>
            <Busy busy={busy.del} className="btn btn-danger" onClick={remove}><Icon d={ICONS.trash} size={14} /> {t("delete")}</Busy>
          </div>
        </section>

        {report && (
          <details className="space-y-2">
            <summary className="help">{t("details")}</summary>
            <div className="help space-y-1">
              <div>{t("dem_used", { src: report.dem?.source, res: num(report.dem?.res_m, 1, lang) })}</div>
              <div>{t("drawn", { roads: report.roads_drawn, water: report.water_pieces, bridges: report.bridges })}</div>
              {report.contour_interval_m > 0 && <div>{t("contours_every", { m: report.contour_interval_m })}</div>}
              {(report.notes || []).map((n, i) => <div key={i}>• {n}</div>)}
              <div>{(report.attribution || []).join(" · ")}</div>
              {m.folder && <div className="mono">{m.folder}</div>}
            </div>
          </details>
        )}
      </div>
      {exportOpen && <ExportDialog model={m} onClose={() => setExportOpen(false)} />}
      {renameOpen && <RenameDialog model={m} onClose={() => setRenameOpen(false)} onDone={reload} />}
    </div>
  );
}

function LogBox({ lines }) {
  const ref = useRef(null);
  useEffect(() => { if (ref.current) ref.current.scrollTop = ref.current.scrollHeight; }, [lines]);
  return <pre ref={ref} className="pre">{lines.join("\n")}</pre>;
}

function ExportDialog({ model, onClose }) {
  const { t, notify } = useApp();
  const [folder, setFolder] = useState("");
  const [what, setWhat] = useState("all");
  const [busy, run] = useBusy();
  const go = () => run("export", async () => {
    const r = await api.call("model_export", { model_id: model.id, folder, what });
    notify(t("exported", { n: r.copied.length, folder: r.folder }));
    onClose();
  });
  return (
    <Modal title={t("export_title")} onClose={onClose} footer={<>
      <button type="button" className="btn" onClick={onClose}>{t("cancel")}</button>
      <Busy busy={busy.export} className="btn btn-primary" onClick={go}>{t("export")}</Busy>
    </>}>
      <label className="block">
        <span className="label">{t("folder")}</span>
        <input className="field mono" value={folder} onChange={(e) => setFolder(e.target.value)} placeholder={t("folder_placeholder")} />
      </label>
      <label className="block">
        <span className="label">{t("what")}</span>
        <select className="field" value={what} onChange={(e) => setWhat(e.target.value)}>
          {["all", "model", "tiles", "3mf", "stl"].map((k) => <option key={k} value={k}>{t(`what_${k}`)}</option>)}
        </select>
      </label>
    </Modal>
  );
}

function RenameDialog({ model, onClose, onDone }) {
  const { t } = useApp();
  const [name, setName] = useState(model.name);
  const [busy, run] = useBusy();
  const go = () => run("rename", async () => {
    if (name.trim() && name.trim() !== model.name) await api.call("model_update", { model_id: model.id, name: name.trim() });
    await onDone();
    onClose();
  });
  return (
    <Modal title={t("rename")} onClose={onClose} footer={<>
      <button type="button" className="btn" onClick={onClose}>{t("cancel")}</button>
      <Busy busy={busy.rename} className="btn btn-primary" onClick={go} disabled={!name.trim()}>{t("save")}</Busy>
    </>}>
      <label className="block">
        <span className="label">{t("name")}</span>
        <input className="field" value={name} maxLength={120} onChange={(e) => setName(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") go(); }} />
      </label>
    </Modal>
  );
}
