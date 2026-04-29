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
    """Sanitize a name for use as a file or folder."""
    return re.sub(r'[<>:"/\\|?*]', '_', name).strip()


def sanitize_gpkg_name(name):
    """Sanitize a name for use as a table in a GeoPackage."""
    s = re.sub(r'[^\w]', '_', name, flags=re.UNICODE)
    s = re.sub(r'_+', '_', s).strip('_')
    return s or "layer"


def get_group_path(layer_node, root):
    """Return the list of parent group names for a layer node."""
    path = []
    parent = layer_node.parent()
    while parent and parent != root:
        if isinstance(parent, QgsLayerTreeGroup) and parent.name():
            path.insert(0, sanitize_filename(parent.name()))
        parent = parent.parent()
    return path


def is_wms(layer):
    """Return True if the layer is a WMS or WMTS service."""
    return layer.providerType() in ('wms', 'wmts')


# ─────────────────────────────────────────────
# EXPORTER CLASS
# ─────────────────────────────────────────────

class Exporter:
    """
    Export a packaged QGIS project to a folder or ZIP.

    Works on a separate instance of QgsProject to avoid
    modifying the user's active session.
    """

    IMG_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.svg', '.bmp',
                      '.tif', '.tiff', '.gif', '.webp'}

    def __init__(self, dest_root, vector_format='gpkg',
                 output_mode='folder', log_callback=None,
                 progress_callback=None):
        self.dest_root = Path(dest_root)
        self.vector_format = vector_format
        self.output_mode = output_mode
        self.log = log_callback or (lambda msg: None)
        self.progress = progress_callback or (lambda c, t: None)

        self.errors = []
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
        original_name = (Path(active_project.fileName()).stem
                         if active_project.fileName() else "project")
        project_name = sanitize_filename(original_name)

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
            self._load_parallel_project(active_project)

            layers_root = work_root / "LAYERS"
            layers_root.mkdir(exist_ok=True)

            self.log("=" * 55)
            self.log(f"  Destination:   {work_root}")
            self.log(f"  Layers in:     {layers_root}")
            self.log(f"  Vector format: {self.vector_format.upper()}")
            self.log("=" * 55)

            self._export_layers(layers_root, project_name)

            self.log("")
            self.log("  — Layout images —")
            self._export_images(work_root)

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
                final_output = Path(zip_path)
                self.log(f"  ✓ ZIP created: {final_output.name}")
            else:
                final_output = work_root

            self._summary(final_output)

        finally:
            if tmp_root and tmp_root.exists():
                shutil.rmtree(tmp_root, ignore_errors=True)
            if self.export_project is not None:
                self.export_project.clear()
                self.export_project = None

    # ─────────────────────────────────────
    # PARALLEL PROJECT
    # ─────────────────────────────────────

    def _load_parallel_project(self, active_project):
        """
        Save the active project to a temporary .qgz and load it into
        a separate QgsProject. This way all subsequent manipulation
        occurs outside the user's session.
        """
        # 1. Save the original state and file path
        original_file = active_project.fileName()
        original_dirty = active_project.isDirty()

        tmp_qgz = tempfile.NamedTemporaryFile(
            suffix='.qgz', delete=False, prefix='qgis_export_src_'
        )
        tmp_qgz.close()
        try:
            # 2. Write to the temporary file
            #    (QGIS will internally change the active project's path)
            active_project.write(tmp_qgz.name)

            # 3. Restore the original path and dirty state immediately,
            #    so the user's session is left untouched
            if original_file:
                active_project.setFileName(original_file)
            else:
                active_project.setFileName("")

            if original_dirty:
                active_project.setDirty(True)

            # 4. Load the parallel project from the temporary file
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

    def _export_layers(self, layers_root, project_name):
        root = self.export_project.layerTreeRoot()
        ctx = QgsCoordinateTransformContext()
        layers = list(root.findLayers())
        total = len(layers)

        gpkg_path = layers_root / f"{project_name}.gpkg"

        for i, layer_node in enumerate(layers, 1):
            self.progress(i, total)

            layer = layer_node.layer()
            if not layer:
                continue

            groups = get_group_path(layer_node, root)
            name = sanitize_filename(layer.name())
            display_path = "LAYERS/" + (
                "/".join(groups + [name]) if groups else name
            )

            if is_wms(layer):
                self.log(f"  ~ [WMS]  {display_path}: kept with original URL")
                self.wms_count += 1
                continue

            if layer.type() == QgsMapLayer.VectorLayer:
                if self.vector_format == 'gpkg':
                    self._export_vector_gpkg(
                        layer, gpkg_path, groups, name, ctx, display_path
                    )
                else:
                    self._export_vector_shp(
                        layer, layers_root, groups, name, ctx, display_path
                    )

            elif layer.type() == QgsMapLayer.RasterLayer:
                self._export_raster(
                    layer, layers_root, groups, name, display_path
                )

            else:
                self.errors.append(
                    f"[???]  {display_path}  →  unrecognized layer type"
                )
                self.log(f"  ⚠ [???]  {display_path}: unrecognized type, skipped")

    def _export_vector_shp(self, layer, layers_root, groups, name, ctx, display_path):
        layer_dir = layers_root.joinpath(*groups) if groups else layers_root
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
            self.log(f"  ✓ [{tag}]  {display_path}.shp")
            self.ok_count += 1
        else:
            self.errors.append(f"[{tag}]  {display_path}  →  {res[1]}")
            self.log(f"  ✗ [{tag}]  {display_path}: {res[1]}")

    def _export_vector_gpkg(self, layer, gpkg_path, groups, name, ctx, display_path):
        provider = layer.providerType()
        tag = "PG " if provider == "postgres" else "VEC"

        # Table name: group_subgroup_layer, with disambiguation
        parts = groups + [name]
        base_name = sanitize_gpkg_name("_".join(parts))
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
            self.log(f"  ✓ [{tag}]  {display_path}  →  {gpkg_path.name}:{layer_name}")
            self.ok_count += 1
        else:
            self.errors.append(f"[{tag}]  {display_path}  →  {res[1]}")
            self.log(f"  ✗ [{tag}]  {display_path}: {res[1]}")

    def _export_raster(self, layer, layers_root, groups, name, display_path):
        layer_dir = layers_root.joinpath(*groups) if groups else layers_root
        layer_dir.mkdir(parents=True, exist_ok=True)

        src_raw = layer.source().split('|')[0]
        if not os.path.isfile(src_raw):
            self.errors.append(
                f"[RAS]  {display_path}  →  source is not a local file"
            )
            self.log(f"  ⚠ [RAS]  {display_path}: non-local source, skipped")
            return

        src_path = Path(src_raw)
        dest_file = layer_dir / f"{name}{src_path.suffix}"
        shutil.copy(src_raw, dest_file)

        # Sidecars: preserve the full suffix (.tif.aux.xml, .tfw, etc.)
        src_resolved = src_path.resolve()
        for aux in src_path.parent.glob(src_path.stem + ".*"):
            if aux.resolve() == src_resolved:
                continue
            full_suffix = aux.name[len(src_path.stem):]
            shutil.copy(aux, layer_dir / f"{name}{full_suffix}")

        layer.setDataSource(str(dest_file), layer.name(), "gdal")
        self.log(f"  ✓ [RAS]  {display_path}{src_path.suffix}")
        self.ok_count += 1

    # ─────────────────────────────────────
    # LAYOUT IMAGES
    # ─────────────────────────────────────

    def _export_images(self, dest_root):
        images_root = dest_root / "IMAGES"
        images_root.mkdir(parents=True, exist_ok=True)

        manager = self.export_project.layoutManager()
        already_copied = {}

        for layout in manager.layouts():
            for item in layout.items():
                if not isinstance(item, QgsLayoutItemPicture):
                    continue

                src = item.picturePath()
                if not src or src.startswith('http') or src.startswith('@'):
                    continue

                src_path = Path(src)
                if src_path.suffix.lower() not in self.IMG_EXTENSIONS:
                    continue

                if not src_path.is_file():
                    self.errors.append(
                        f"[IMG]  {layout.name()} → '{src_path.name}': "
                        f"file not found"
                    )
                    self.log(
                        f"  ⚠ [IMG]  Layout '{layout.name()}' → "
                        f"'{src_path.name}': not found"
                    )
                    continue

                src_resolved = src_path.resolve()

                # Already copied → just update the path
                if src_resolved in already_copied:
                    dest_file = already_copied[src_resolved]
                    item.setPicturePath(str(dest_file))
                    self.log(
                        f"  = [IMG]  Layout '{layout.name()}' → "
                        f"IMAGES/{dest_file.name} (reused)"
                    )
                    continue

                # First time → copy and register
                dest_file = images_root / src_path.name

                # If a file with that name exists from a different source,
                # rename to avoid collisions
                if dest_file.exists():
                    stem, suffix = src_path.stem, src_path.suffix
                    counter = 1
                    while dest_file.exists():
                        dest_file = images_root / f"{stem}_{counter}{suffix}"
                        counter += 1

                shutil.copy(src_path, dest_file)
                already_copied[src_resolved] = dest_file
                item.setPicturePath(str(dest_file))
                self.log(
                    f"  ✓ [IMG]  Layout '{layout.name()}' → "
                    f"IMAGES/{dest_file.name}"
                )
                self.img_ok += 1

    # ─────────────────────────────────────
    # SUMMARY
    # ─────────────────────────────────────

    def _summary(self, output):
        self.log("")
        self.log("=" * 55)
        self.log(f"  ✓ {self.ok_count} layers exported")
        self.log(f"  ✓ {self.img_ok} images exported")
        self.log(f"  ~ {self.wms_count} WMS layers kept with original URL")
        self.log(f"  ✓ Output: {output}")
        if self.errors:
            self.log(f"  ⚠ {len(self.errors)} warning(s):")
            for e in self.errors:
                self.log(f"      {e}")
        self.log("=" * 55)
