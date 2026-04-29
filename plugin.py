import os

from qgis.PyQt.QtWidgets import QAction
from qgis.PyQt.QtGui import QIcon

from .dialog import ExportarProyectoDialog


class ExportarProyectoPlugin:

    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.menu_name = "&QShare"
        self.plugin_dir = os.path.dirname(__file__)

    def initGui(self):
        icon_path = os.path.join(self.plugin_dir, "icon.png")
        icon = QIcon(icon_path) if os.path.isfile(icon_path) else QIcon()
        self.action = QAction(
            icon,
            "Export project…",
            self.iface.mainWindow(),
        )
        self.action.triggered.connect(self.run)
        self.iface.addPluginToMenu(self.menu_name, self.action)
        self.iface.addToolBarIcon(self.action)

    def unload(self):
        if self.action:
            self.iface.removePluginMenu(self.menu_name, self.action)
            self.iface.removeToolBarIcon(self.action)
            self.action = None

    def run(self):
        dlg = ExportarProyectoDialog(self.iface.mainWindow())
        dlg.exec()
