from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QRadioButton, QGroupBox, QTextEdit,
    QProgressBar, QFileDialog, QMessageBox, QDialogButtonBox,
    QApplication,
)
from qgis.PyQt.QtGui import QFont

from .exporter import Exporter


class ExportarProyectoDialog(QDialog):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Export packaged project")
        self.resize(660, 540)
        self._build_ui()

    # ─────────────────────────────────────
    # UI
    # ─────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # Carpeta destino
        layout.addWidget(QLabel("<b>Destination folder</b>"))
        h = QHBoxLayout()
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("Select a folder…")
        btn_browse = QPushButton("Browse…")
        btn_browse.clicked.connect(self._on_browse)
        h.addWidget(self.path_edit)
        h.addWidget(btn_browse)
        layout.addLayout(h)

        # Formato vectorial
        gb_format = QGroupBox("Vector layer format")
        gl = QHBoxLayout(gb_format)
        self.rb_gpkg = QRadioButton("GeoPackage (a single .gpkg)")
        self.rb_shp = QRadioButton("Shapefile (one .shp per layer)")
        self.rb_gpkg.setChecked(True)
        gl.addWidget(self.rb_gpkg)
        gl.addWidget(self.rb_shp)
        gl.addStretch()
        layout.addWidget(gb_format)

        # Modo de salida
        gb_output = QGroupBox("Output")
        ol = QHBoxLayout(gb_output)
        self.rb_folder = QRadioButton("Folder")
        self.rb_zip = QRadioButton(".zip file")
        self.rb_folder.setChecked(True)
        ol.addWidget(self.rb_folder)
        ol.addWidget(self.rb_zip)
        ol.addStretch()
        layout.addWidget(gb_output)

        # Log
        layout.addWidget(QLabel("<b>Log</b>"))
        self.log_edit = QTextEdit()
        self.log_edit.setReadOnly(True)
        mono = QFont("Monospace")
        mono.setStyleHint(QFont.StyleHint.TypeWriter)
        self.log_edit.setFont(mono)
        layout.addWidget(self.log_edit, 1)

        # Progreso
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        # Botones
        bb = QDialogButtonBox()
        self.btn_run = bb.addButton(
            "Export", QDialogButtonBox.ButtonRole.AcceptRole
        )
        self.btn_close = bb.addButton(
            "Close", QDialogButtonBox.ButtonRole.RejectRole
        )
        self.btn_run.clicked.connect(self._on_run)
        self.btn_close.clicked.connect(self.reject)
        layout.addWidget(bb)

    # ─────────────────────────────────────
    # ACCIONES
    # ─────────────────────────────────────

    def _on_browse(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Select destination folder"
        )
        if folder:
            self.path_edit.setText(folder)

    def _on_run(self):
        dest = self.path_edit.text().strip()
        if not dest:
            QMessageBox.warning(
                self, "Missing folder", "Please select a destination folder."
            )
            return

        vector_format = "gpkg" if self.rb_gpkg.isChecked() else "shp"
        output_mode = "zip" if self.rb_zip.isChecked() else "folder"

        self.log_edit.clear()
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.btn_run.setEnabled(False)
        self.btn_close.setEnabled(False)

        try:
            exporter = Exporter(
                dest_root=dest,
                vector_format=vector_format,
                output_mode=output_mode,
                log_callback=self._append_log,
                progress_callback=self._update_progress,
            )
            exporter.run()
            QMessageBox.information(
                self,
                "Export finished",
                "The export has been completed.",
            )
        except Exception as e:
            self._append_log(f"\n  ✗ Error: {e}")
            QMessageBox.critical(self, "Export error", str(e))
        finally:
            self.progress_bar.setVisible(False)
            self.btn_run.setEnabled(True)
            self.btn_close.setEnabled(True)

    # ─────────────────────────────────────
    # CALLBACKS
    # ─────────────────────────────────────

    def _append_log(self, msg):
        self.log_edit.append(msg)
        QApplication.processEvents()

    def _update_progress(self, current, total):
        if total > 0:
            self.progress_bar.setMaximum(total)
            self.progress_bar.setValue(current)
        QApplication.processEvents()