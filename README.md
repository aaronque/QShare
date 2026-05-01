# QShare

A minimalist QGIS plugin to export a packaged copy of your project — layers, layout images and the `.qgz` file — to a folder or a ZIP archive, ready to be shared with anyone.

No external dependencies. No bloat. It does one thing and does it well.

## Why another one?

There are several QGIS plugins that package projects for sharing, and most of them try to do too much: extra dialogs, conversion options nobody asked for, fragile behaviour with rasters or layouts, broken layer paths after extraction. QShare is the opposite — a single dialog, two choices, and a project that just works when the recipient opens it.

## Features

- Exports all vector layers either as **Shapefile** (one `.shp` per layer) or as a **single GeoPackage** (`.gpkg`) containing every layer.
- Copies all local raster layers along with their sidecar files (`.aux.xml`, `.tfw`, `.prj`, etc.).
- Copies every image used in print layouts to a dedicated `IMAGENES/` folder, deduplicating images reused across multiple layouts.
- Keeps WMS/WMTS layers with their original URLs.
- Rewrites all paths in the `.qgz` as relative, so the package is portable.
- Outputs as a plain folder or as a `.zip` archive.
- **Does not modify the active project** in QGIS. The export runs against an isolated copy of the project, so your current session is left untouched.

## Output structure

```
MyProject/
├── MyProject.qgz
├── LAYERS/
│   ├── MyProject.gpkg          (if GeoPackage was selected)
│   └── ...                     (or .shp files mirroring the layer tree)
└── IMAGES/
    └── ...                     (images from print layouts)
```

If you choose ZIP output, the resulting `MyProject.zip` contains exactly that structure at its root.

## Installation

### From the QGIS Plugin Repository

Install it from **Plugins → Manage and Install Plugins → All**, search for "QShare".

### Manual installation

1. Download or clone this repository.
2. Copy the `QShare` folder into your QGIS plugin directory:
   - **Linux:** `~/.local/share/QGIS/QGISX/profiles/default/python/plugins/`
   - **Windows:** `%APPDATA%\QGIS\QGISX\profiles\default\python\plugins\`
   - **macOS:** `~/Library/Application Support/QGIS/QGISX/profiles/default/python/plugins/`
3. Restart QGIS (or reload plugins).
4. Enable **QShare** under **Plugins → Manage and Install Plugins → Installed**.

## Usage

1. Open the project you want to share in QGIS.
2. Launch QShare from the **Plugins** menu or the toolbar icon.
3. Pick a destination folder.
4. Choose the vector format — GeoPackage or Shapefile.
5. Choose whether to output a folder or a `.zip` archive.
6. Click **Export**.

The dialog shows a live log and a progress bar. When it finishes, your shareable copy is in the chosen destination.

## Compatibility

- QGIS 3.22 or newer.
- Compatible with both **Qt5** (QGIS 3.x) and **Qt6** (QGIS 4.x). All Qt imports go through the `qgis.PyQt` abstraction layer.

## Limitations

- PostGIS and other database-backed vector layers are exported as files (SHP or GPKG); the database connection is replaced with a local data source in the exported project.
- Memory layers are exported as files.
- Non-local rasters (e.g. remote URLs other than WMS/WMTS) are skipped with a warning.
- Some advanced layer styling features (e.g. very specific symbology referencing absolute paths) may need manual review after export.

## Contributing

Bug reports and feature requests are welcome on the [issue tracker](https://github.com/aaronque/QShare/issues). Pull requests are also welcome — please keep the plugin's minimalist philosophy in mind.

## License

QShare is released under the GNU General Public License v2 or later. See the [LICENSE](LICENSE) file for details.

## Author

Created by [Aarón Quesada](https://github.com/aaronque).
