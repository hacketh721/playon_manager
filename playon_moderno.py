import sqlite3
from datetime import datetime
from typing import Optional, Dict, List

# ============================================================================
# UTILITY CLASSES
# ============================================================================

class LoadingOverlay:
    """Overlay para mostrar estado de carga"""
    def __init__(self, parent=None):
        self.parent = parent
        self.is_visible = False

    def show(self):
        """Muestra el overlay de carga"""
        self.is_visible = True

    def hide(self):
        """Oculta el overlay de carga"""
        self.is_visible = False


class MessageUtils:
    """Utilidades para mostrar mensajes"""
    @staticmethod
    def show_info(title: str, message: str):
        """Muestra mensaje de información"""
        print(f"[INFO] {title}: {message}")

    @staticmethod
    def show_warning(title: str, message: str):
        """Muestra mensaje de advertencia"""
        print(f"[WARNING] {title}: {message}")

    @staticmethod
    def show_error(title: str, message: str):
        """Muestra mensaje de error"""
        print(f"[ERROR] {title}: {message}")


# ============================================================================
# DATABASE MANAGER
# ============================================================================

class DatabaseManager:
    def __init__(self, db_path):
        self.conn = sqlite3.connect(db_path)
        self.conn.execute("PRAGMA foreign_keys = ON;")  # Ensure referential integrity
        self.create_tables()

    def create_tables(self):
        self.conn.execute('''CREATE TABLE IF NOT EXISTS expedientes (
            id INTEGER PRIMARY KEY,
            estado TEXT CHECK(estado IN ('ABIERTO', 'CERRADO')),
            cerrado_por TEXT,
            cerrado_en TIMESTAMP,
            ...
        )''')
        self.conn.execute('''CREATE TABLE IF NOT EXISTS historial (
            id INTEGER PRIMARY KEY,
            expediente_id INTEGER,
            accion TEXT,
            fecha TIMESTAMP,
            FOREIGN KEY (expediente_id) REFERENCES expedientes (id) ON DELETE CASCADE
        )''')
        self.conn.commit()

    def close_expediente(self, expediente_id, cerrado_por):
        self.conn.execute('''UPDATE expedientes SET estado = 'CERRADO', cerrado_por = ?, cerrado_en = ? WHERE id = ?''',
                          (cerrado_por, datetime.now(), expediente_id))
        self.record_historial(expediente_id, 'CERRADO')
        self.conn.commit()

    def reopen_expediente(self, expediente_id):
        self.conn.execute('''UPDATE expedientes SET estado = 'ABIERTO', cerrado_por = NULL, cerrado_en = NULL WHERE id = ?''',
                          (expediente_id,))
        self.record_historial(expediente_id, 'REABIERTO')
        self.conn.commit()

    def get_expediente_stats(self):
        resultado = self.conn.execute('''SELECT estado, COUNT(*) FROM expedientes GROUP BY estado''').fetchall()
        return dict(resultado)

    def get_expediente_historial(self, expediente_id):
        return self.conn.execute('''SELECT * FROM historial WHERE expediente_id = ?''', (expediente_id,)).fetchall()

    def record_historial(self, expediente_id, accion):
        self.conn.execute('''INSERT INTO historial (expediente_id, accion, fecha) VALUES (?, ?, ?)''',
                          (expediente_id, accion, datetime.now()))

    def __del__(self):
        self.conn.close()


# ============================================================================
# MAIN WINDOW
# ============================================================================

class MainWindow:
    """Ventana principal de la aplicación"""
    def __init__(self, current_user: str):
        self.current_user = current_user
        self.db_manager = DatabaseManager('playon.db')
        self.loading_overlay = LoadingOverlay(self)
        self.initialize_ui()

    def initialize_ui(self):
        """Inicializa la interfaz de usuario"""
        MessageUtils.show_info("Inicialización", f"Bienvenido {self.current_user}")
        self.pass_user_to_views()

    def pass_user_to_views(self):
        """Pasa el usuario actual a todas las vistas"""
        # Aquí se pueden pasar el usuario a diferentes vistas/componentes
        print(f"Usuario actual en vistas: {self.current_user}")

    def show_loading(self):
        """Muestra overlay de carga"""
        self.loading_overlay.show()

    def hide_loading(self):
        """Oculta overlay de carga"""
        self.loading_overlay.hide()


# ============================================================================
# APPLICATION ENTRY POINT
# ============================================================================

if __name__ == '__main__':
    current_user = 'hacketh7213'
    main_window = MainWindow(current_user)