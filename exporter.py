import os
import re
import shutil
import tempfile
from pathlib import Path

from qgis.core import (
    QgsProject, QgsMapLayer, QgsLayerTreeGroup,
    QgsVectorFileWriter, QgsCoordinateTransformContext,
    QgsLayoutItemPicture,
)


# ─────────────────────────────────────────────
# UTILITIES
# ─────────────────────────────────────────────

def sanitize_filename(name):
    """Sanitizes a name for use as a file or folder."""
    return re.sub(r'[<>:"/\\|?*]', '_', name).strip()


def sanitize_gpkg_name(name):
    """Sanitizes a name for use as a table in GeoPackage."""
    s = re.sub(r'[^\w]', '_', name, flags=re.UNICODE)
    s = re.sub(r'_+', '_', s).strip('_')
    return s or "layer"


def get_group_path(layer_node, root):
    path = []
    parent = layer_node.parent()
    while parent and parent != root:
        if isinstance(parent, QgsLayerTreeGroup) and parent.name():
            path.insert(0, sanitize_filename(parent.name()))
        parent = parent.parent()
    return path


def es_wms(layer):
    return layer.providerType() in ('wms', 'wmts')


# ─────────────────────────────────────────────
# EXPORTER CLASS
# ─────────────────────────────────────────────

class Exporter:
    """
    Exports a packaged QGIS project to a folder or ZIP.

    Works on a separate instance of QgsProject to avoid
    modifying the user's active session.
    """

    EXTENSIONES_IMG = {'.png', '.jpg', '.jpeg', '.svg', '.bmp',
                       '.tif', '.tiff', '.gif', '.webp'}

    def __init__(self, dest_root, vector_format='gpkg',
                 output_mode='folder', log_callback=None,
                 progress_callback=None):
        self.dest_root = Path(dest_root)
        self.vector_format = vector_format
        self.output_mode = output_mode
        self.log = log_callback or (lambda msg: None)
        self.progress = progress_callback or (lambda c, t: None)

        self.errores = []
        self.ok_count = 0
        self.wms_count = 0
        self.img_ok = 0

        self.export_project = None
        self._gpkg_initialized = False
        self._used_gpkg_names = set()

    # ─────────────────────────────────────
    # ENTRY POINT
    # ─────────────────────────────────────

    def run(self):
        active_project = QgsProject.instance()
        nombre_orig = (Path(active_project.fileName()).stem
                       if active_project.fileName() else "project")
        project_name = sanitize_filename(nombre_orig)

        # ZIP → work in a temporary directory and compress at the end
        # Folder → work directly in dest_root/<name>
        if self.output_mode == 'zip':
            tmp_root = Path(tempfile.mkdtemp(prefix='qgis_export_'))
            work_root = tmp_root / project_name
            work_root.mkdir()
        else:
            work_root = self.dest_root / project_name
            work_root.mkdir(parents=True, exist_ok=True)
            tmp_root = None

        try:
            self._cargar_proyecto_paralelo(active_project)

            capas_root = work_root / "LAYERS"
            capas_root.mkdir(exist_ok=True)

            self.log("=" * 55)
            self.log(f"  Destination:  {work_root}")
            self.log(f"  Layers in: {capas_root}")
            self.log(f"  Vector format: {self.vector_format.upper()}")
            self.log("=" * 55)

            self._exportar_capas(capas_root, project_name)

            self.log("")
            self.log("  — Layout images —")
            self._exportar_imagenes(work_root)

            # Relative paths + save .qgz
            self.export_project.writeEntry("Paths", "/Absolute", "false")
            dest_qgz = work_root / f"{project_name}.qgz"
            self.export_project.setFileName(str(dest_qgz))
            self.export_project.write()
            self.log(f"  ✓ Project saved: {dest_qgz.name}")

            # Compress if necessary
            if self.output_mode == 'zip':
                self.log("")
                self.log("  — Compressing —")
                zip_base = self.dest_root / project_name
                zip_path = shutil.make_archive(
                    base_name=str(zip_base),
                    format='zip',
                    root_dir=str(tmp_root),
                    base_dir=project_name,
                )
                salida_final = Path(zip_path)
                self.log(f"  ✓ ZIP created: {salida_final.name}")
            else:
                salida_final = work_root

            self._resumen(salida_final)

        finally:
            if tmp_root and tmp_root.exists():
                shutil.rmtree(tmp_root, ignore_errors=True)
            if self.export_project is not None:
                self.export_project.clear()
                self.export_project = None

    # ─────────────────────────────────────
    # PARALLEL PROJECT
    # ─────────────────────────────────────

    def _cargar_proyecto_paralelo(self, active_project):
        """
        Saves the active project to a temporary .qgz and loads it into
        a separate QgsProject. This way all subsequent manipulation
        occurs outside the session.
        """
        # 1. Guardamos el estado y la ruta original
        original_file = active_project.fileName()
        original_dirty = active_project.isDirty()

        tmp_qgz = tempfile.NamedTemporaryFile(
            suffix='.qgz', delete=False, prefix='qgis_export_src_'
        )
        tmp_qgz.close()
        try:
            # 2. Escribimos al temporal (QGIS cambiará la ruta activa internamente)
            active_project.write(tmp_qgz.name)
            
            # 3. ¡Solución! Restauramos la ruta y el estado original al instante
            if original_file:
                active_project.setFileName(original_file)
            else:
                active_project.setFileName("")
                
            if original_dirty:
                active_project.setDirty(True)

            # 4. Cargamos el proyecto paralelo
            self.export_project = QgsProject()
            self.export_project.read(tmp_qgz.name)
        finally:
            try:
                os.unlink(tmp_qgz.name)
            except OSError:
                pass

    # ─────────────────────────────────────
    # LAYERS
    # ─────────────────────────────────────

    def _exportar_capas(self, capas_root, project_name):
        root = self.export_project.layerTreeRoot()
        ctx = QgsCoordinateTransformContext()
        layers = list(root.findLayers())
        total = len(layers)

        gpkg_path = capas_root / f"{project_name}.gpkg"

        for i, layer_node in enumerate(layers, 1):
            self.progress(i, total)

            layer = layer_node.layer()
            if not layer:
                continue

            grupos = get_group_path(layer_node, root)
            name = sanitize_filename(layer.name())
            ruta_visual = "LAYERS/" + (
                "/".join(grupos + [name]) if grupos else name
            )

            if es_wms(layer):
                self.log(f"  ~ [WMS]  {ruta_visual}: kept with original URL")
                self.wms_count += 1
                continue

            if layer.type() == QgsMapLayer.VectorLayer:
                if self.vector_format == 'gpkg':
                    self._exportar_vector_gpkg(
                        layer, gpkg_path, grupos, name, ctx, ruta_visual
                    )
                else:
                    self._exportar_vector_shp(
                        layer, capas_root, grupos, name, ctx, ruta_visual
                    )

            elif layer.type() == QgsMapLayer.RasterLayer:
                self._exportar_raster(
                    layer, capas_root, grupos, name, ruta_visual
                )

            else:
                self.errores.append(
                    f"[???]  {ruta_visual}  →  unrecognized layer type"
                )
                self.log(f"  ⚠ [???]  {ruta_visual}: unrecognized type, skipped")

    def _exportar_vector_shp(self, layer, capas_root, grupos, name, ctx, ruta_visual):
        layer_dir = capas_root.joinpath(*grupos) if grupos else capas_root
        layer_dir.mkdir(parents=True, exist_ok=True)
        dest_file = layer_dir / f"{name}.shp"

        provider = layer.providerType()
        tag = "PG " if provider == "postgres" else "VEC"

        opts = QgsVectorFileWriter.SaveVectorOptions()
        opts.driverName = "ESRI Shapefile"
        opts.fileEncoding = "UTF-8"

        res = QgsVectorFileWriter.writeAsVectorFormatV3(
            layer, str(dest_file), ctx, opts
        )
        if res[0] == QgsVectorFileWriter.NoError:
            layer.setDataSource(str(dest_file), layer.name(), "ogr")
            self.log(f"  ✓ [{tag}]  {ruta_visual}.shp")
            self.ok_count += 1
        else:
            self.errores.append(f"[{tag}]  {ruta_visual}  →  {res[1]}")
            self.log(f"  ✗ [{tag}]  {ruta_visual}: {res[1]}")

    def _exportar_vector_gpkg(self, layer, gpkg_path, grupos, name, ctx, ruta_visual):
        provider = layer.providerType()
        tag = "PG " if provider == "postgres" else "VEC"

        # Table name: group_subgroup_layer, with disambiguation
        partes = grupos + [name]
        base_name = sanitize_gpkg_name("_".join(partes))
        layer_name = base_name
        counter = 1
        while layer_name in self._used_gpkg_names:
            counter += 1
            layer_name = f"{base_name}_{counter}"
        self._used_gpkg_names.add(layer_name)

        opts = QgsVectorFileWriter.SaveVectorOptions()
        opts.driverName = "GPKG"
        opts.fileEncoding = "UTF-8"
        opts.layerName = layer_name

        if not self._gpkg_initialized:
            opts.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteFile
        else:
            opts.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteLayer

        res = QgsVectorFileWriter.writeAsVectorFormatV3(
            layer, str(gpkg_path), ctx, opts
        )
        if res[0] == QgsVectorFileWriter.NoError:
            self._gpkg_initialized = True
            new_uri = f"{gpkg_path}|layername={layer_name}"
            layer.setDataSource(new_uri, layer.name(), "ogr")
            self.log(f"  ✓ [{tag}]  {ruta_visual}  →  {gpkg_path.name}:{layer_name}")
            self.ok_count += 1
        else:
            self.errores.append(f"[{tag}]  {ruta_visual}  →  {res[1]}")
            self.log(f"  ✗ [{tag}]  {ruta_visual}: {res[1]}")

    def _exportar_raster(self, layer, capas_root, grupos, name, ruta_visual):
        layer_dir = capas_root.joinpath(*grupos) if grupos else capas_root
        layer_dir.mkdir(parents=True, exist_ok=True)

        src_raw = layer.source().split('|')[0]
        if not os.path.isfile(src_raw):
            self.errores.append(
                f"[RAS]  {ruta_visual}  →  source is not a local file"
            )
            self.log(f"  ⚠ [RAS]  {ruta_visual}: non-local source, skipped")
            return

        src_path = Path(src_raw)
        dest_file = layer_dir / f"{name}{src_path.suffix}"
        shutil.copy(src_raw, dest_file)

        # Sidecars: preserve the full suffix (.tif.aux.xml, .tfw, etc.)
        src_resolved = src_path.resolve()
        for aux in src_path.parent.glob(src_path.stem + ".*"):
            if aux.resolve() == src_resolved:
                continue
            sufijo_completo = aux.name[len(src_path.stem):]
            shutil.copy(aux, layer_dir / f"{name}{sufijo_completo}")

        layer.setDataSource(str(dest_file), layer.name(), "gdal")
        self.log(f"  ✓ [RAS]  {ruta_visual}{src_path.suffix}")
        self.ok_count += 1

    # ─────────────────────────────────────
    # LAYOUT IMAGES
    # ─────────────────────────────────────

    def _exportar_imagenes(self, dest_root):
        imagenes_root = dest_root / "IMAGES"
        imagenes_root.mkdir(parents=True, exist_ok=True)

        manager = self.export_project.layoutManager()
        ya_copiados = {}

        for layout in manager.layouts():
            for item in layout.items():
                if not isinstance(item, QgsLayoutItemPicture):
                    continue

                src = item.picturePath()
                if not src or src.startswith('http') or src.startswith('@'):
                    continue

                src_path = Path(src)
                if src_path.suffix.lower() not in self.EXTENSIONES_IMG:
                    continue

                if not src_path.is_file():
                    self.errores.append(
                        f"[IMG]  {layout.name()} → '{src_path.name}': "
                        f"file not found"
                    )
                    self.log(
                        f"  ⚠ [IMG]  Layout '{layout.name()}' → "
                        f"'{src_path.name}': not found"
                    )
                    continue

                src_resolved = src_path.resolve()

                if src_resolved in ya_copiados:
                    dest_file = ya_copiados[src_resolved]
                    item.setPicturePath(str(dest_file))
                    self.log(
                        f"  = [IMG]  Layout '{layout.name()}' → "
                        f"IMAGES/{dest_file.name} (reused)"
                    )
                    continue

                dest_file = imagenes_root / src_path.name
                if dest_file.exists():
                    stem, suffix = src_path.stem, src_path.suffix
                    counter = 1
                    while dest_file.exists():
                        dest_file = imagenes_root / f"{stem}_{counter}{suffix}"
                        counter += 1

                shutil.copy(src_path, dest_file)
                ya_copiados[src_resolved] = dest_file
                item.setPicturePath(str(dest_file))
                self.log(
                    f"  ✓ [IMG]  Layout '{layout.name()}' → "
                    f"IMAGES/{dest_file.name}"
                )
                self.img_ok += 1

    # ─────────────────────────────────────
    # SUMMARY
    # ─────────────────────────────────────

    def _resumen(self, salida):
        self.log("")
        self.log("=" * 55)
        self.log(f"  ✓ {self.ok_count} layers exported")
        self.log(f"  ✓ {self.img_ok} images exported")
        self.log(f"  ~ {self.wms_count} WMS layers kept with original URL")
        self.log(f"  ✓ Output: {salida}")
        if self.errores:
            self.log(f"  ⚠ {len(self.errores)} warning(s):")
            for e in self.errores:
                self.log(f"      {e}")
        self.log("=" * 55)
