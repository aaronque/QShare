def classFactory(iface):
    from .plugin import ExportarProyectoPlugin
    return ExportarProyectoPlugin(iface)