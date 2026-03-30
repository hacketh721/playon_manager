# =============================================================================
# GESTOR DE FLOTA AUTOMOTOR - UI REFACTORIZADA (2026)
# Modernización completa de la interfaz manteniendo toda la lógica existente
# =============================================================================

import os
import re
import shutil
import sqlite3
import configparser
import logging
import threading
import json
from datetime import datetime
from pathlib import Path
import webbrowser
import pandas as pd
from PIL import Image, ImageTk
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, Toplevel, Label
from docxtpl import DocxTemplate
import win32com.client
from reportlab.lib.pagesizes import A4, legal
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.platypus import Table, TableStyle, Paragraph
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.utils import ImageReader, simpleSplit
from reportlab.lib.enums import TA_CENTER
import pytesseract
import pdfplumber
from pdf2image import convert_from_path

# =============================================================================
# CONFIGURACIÓN DE TTKBOOTSTRAP PARA UI MODERNA
# =============================================================================
try:
    import ttkbootstrap as ttkb
    from ttkbootstrap.constants import *
    USE_TTKBOOTSTRAP = True
except ImportError:
    USE_TTKBOOTSTRAP = False
    print("⚠️ ttkbootstrap no encontrado. Instalá: pip install ttkbootstrap")

# =============================================================================
# CONFIGURACIÓN OCR (TESSERACT)
# =============================================================================
PATH_TESSERACT = r'C:\Program Files\Tesseract-OCR\tesseract.exe'
if os.path.exists(PATH_TESSERACT):
    pytesseract.pytesseract.tesseract_cmd = PATH_TESSERACT

PATH_POPPLER = r'C:\poppler\Library\bin'

# =============================================================================
# CONSTANTES GLOBALES
# =============================================================================
DEFAULT_ACLARACIONES = {
    "foto": "Se deja constancia que al momento de efectuar la inspección, el vehículo carece de chapa patente por extravío...",
    "motor": "Se deja constancia que al momento de efectuar la inspección... número {motor_numero}...",
    "chasis": "El plano correspondiente al grabado presenta signos de corrosión... número {chasis_numero}...",
    "docu": "Se deja constancia que la imagen adjunta correspondiente a la documentación del vehículo presenta deterioro..."
}

# =============================================================================
# CONFIGURACIÓN Y LOGGING
# =============================================================================
class Config:
    def __init__(self, config_file="config.ini"):
        self.config = configparser.ConfigParser()
        script_dir = Path(os.path.dirname(os.path.abspath(__file__)))
        self.config_file = script_dir / config_file
        self._setup_defaults(script_dir)
        self._load_or_create_config()

    def _setup_defaults(self, base_dir_path):
        self.config['PATHS'] = {
            'base_dir': str(base_dir_path),
            'data_dir': 'data',
            'output_dir': 'output',
            'playon_dir': 'playon',
            'logo_file': 'logo.png',
            'db_file': 'flota.db',
            'plantilla_inventario_file': 'plantilla_inventario.docx',
            'plantilla_informe_tecnico_file': 'ITPLANTILLA.docx'
        }
        self.config['IMAGE'] = {
            'max_width': '1600',
            'max_height': '1600',
            'quality': '85',
            'preview_width': '150',
            'preview_height': '150'
        }
        self.config['PDF'] = {
            'max_image_width_cm': '19.0',
            'max_image_height_cm': '24.0'
        }
        self.config['LOGGING'] = {
            'level': 'INFO',
            'format': '%(asctime)s - %(levelname)s - %(message)s',
            'file': 'flota.log'
        }

    def _load_or_create_config(self):
        if os.path.exists(self.config_file):
            self.config.read(self.config_file)
        else:
            self._save_config()

    def _save_config(self):
        with open(self.config_file, 'w', encoding='utf-8') as f:
            self.config.write(f)

    def get_path(self, key):
        base = Path(self.config['PATHS']['base_dir'])
        if key == 'base_dir':
            return base
        return base / self.config['PATHS'][key]

    def get_absolute_path(self, relative_path):
        if not relative_path:
            return None
        return self.get_path('base_dir') / relative_path

    def get_relative_path(self, absolute_path):
        if not absolute_path:
            return None
        try:
            return Path(absolute_path).relative_to(self.get_path('base_dir'))
        except ValueError:
            return Path(absolute_path)


class Logger:
    def __init__(self, config):
        self.config = config
        self._setup_logging()

    def _setup_logging(self):
        try:
            log_level = getattr(logging, self.config.config['LOGGING']['level'])
            log_format = self.config.config['LOGGING']['format']
            log_file = self.config.get_path('base_dir') / self.config.config['LOGGING']['file']
        except (configparser.Error, KeyError):
            log_level = logging.INFO
            log_format = '%(asctime)s - %(levelname)s - %(message)s'
            log_file = self.config.get_path('base_dir') / 'flota.log'
        
        logging.basicConfig(
            level=log_level,
            format=log_format,
            handlers=[
                logging.FileHandler(log_file, encoding='utf-8'),
                logging.StreamHandler()
            ],
            force=True
        )
        self.logger = logging.getLogger(__name__)
        self.logger.info("Sistema de logging inicializado")


# Inicialización global
config = Config()
logger_setup = Logger(config)
logger = logging.getLogger(__name__)

# =============================================================================
# FUNCIONES DE APOYO
# =============================================================================
def safe_slug(text):
    if not text:
        return "sin_nombre"
    text = str(text).strip().lower()
    text = re.sub(r'[^a-z0-9]+', '_', text)
    return text[:50] or "sin_nombre"


def limpiar_interno_val(val):
    if val is None:
        return None
    s_val = str(val).strip().lower()
    if not s_val or s_val == 'nan':
        return None
    try:
        return int(float(s_val))
    except (ValueError, TypeError):
        if s_val != 'nan':
            logger.warning(f"Error limpiando interno '{val}': {str(val)}")
        return None


# =============================================================================
# MANEJADOR DE RUTAS
# =============================================================================
class PathManager:
    @staticmethod
    def ensure_directories():
        dirs = ['data_dir', 'output_dir', 'playon_dir']
        for dir_key in dirs:
            path = config.get_path(dir_key)
            path.mkdir(parents=True, exist_ok=True)
            logger.info(f"Directorio asegurado: {path}")

    @staticmethod
    def get_vehicle_folder(interno, dominio, marca, modelo):
        data_path = config.get_path('data_dir')
        interno_str = str(interno)
        interno_str_zfill = interno_str.zfill(4)
        
        existing_folders = (
            list(data_path.glob(f"{interno_str}_*")) +
            list(data_path.glob(f"{interno_str_zfill}_*")) +
            list(data_path.glob(f"{interno_str}"))
        )

        if existing_folders:
            found_path = existing_folders[0]
            found_path.mkdir(parents=True, exist_ok=True)
            return found_path
        
        nombre_nuevo = f"{interno_str_zfill}_{safe_slug(dominio)}_{safe_slug(marca)}_{safe_slug(modelo)}"
        ruta_nueva = data_path / nombre_nuevo
        ruta_nueva.mkdir(parents=True, exist_ok=True)
        return ruta_nueva

    @staticmethod
    def copy_and_process_file(origen, destino_folder, nombre_base):
        origen_path = Path(origen)
        ext = origen_path.suffix.lower()
        destino_path = destino_folder / f"{nombre_base}{ext}"
        
        try:
            shutil.copy2(origen, destino_path)
            logger.info(f"Archivo copiado: {origen} -> {destino_path}")
            
            if ext in ('.jpg', '.jpeg', '.png'):
                ImageProcessor.resize_and_optimize(destino_path)
            
            return destino_path
        except Exception as e:
            logger.error(f"Error copiando archivo {origen}: {str(e)}")
            raise


# =============================================================================
# PROCESADOR DE IMÁGENES
# =============================================================================
class ImageProcessor:
    @staticmethod
    def validate_image(path):
        if not path or not Path(path).exists():
            return False
        try:
            with Image.open(path) as img:
                img.verify()
            return True
        except Exception:
            return False

    @staticmethod
    def is_image(path):
        if not path:
            return False
        ext = Path(path).suffix.lower()
        return ext in (".jpg", ".jpeg", ".png")

    @staticmethod
    def resize_and_optimize(path):
        try:
            max_w = int(config.config['IMAGE']['max_width'])
            max_h = int(config.config['IMAGE']['max_height'])
            quality = int(config.config['IMAGE']['quality'])
            
            with Image.open(path) as im:
                im = im.convert("RGB") if im.mode in ("P", "RGBA", "LA") else im
                w, h = im.size
                ratio = min(max_w / w, max_h / h, 1.0)
                
                if ratio < 1.0:
                    new_size = (int(w * ratio), int(h * ratio))
                    im = im.resize(new_size, Image.Resampling.LANCZOS)
                
                ext = Path(path).suffix.lower()
                if ext in (".jpg", ".jpeg"):
                    im.save(path, format="JPEG", quality=quality, optimize=True)
                elif ext == ".png":
                    im.save(path, format="PNG", optimize=True)
                else:
                    jpg_path = Path(path).with_suffix('.jpg')
                    im.save(jpg_path, format="JPEG", quality=quality, optimize=True)
                    Path(path).unlink(missing_ok=True)
                    return jpg_path
                
                return path
        except Exception as e:
            logger.error(f"Error procesando imagen {path}: {str(e)}")
            return path


# =============================================================================
# BASE DE DATOS
# =============================================================================
class DatabaseManager:
    def __init__(self, db_filename=None):
        if db_filename is None:
            filename = config.config['PATHS']['db_file']
        else:
            filename = db_filename
        self.db_path = config.get_path('base_dir') / filename
        self.current_version = 5

    def get_connection(self):
        return sqlite3.connect(self.db_path)

    def init_database(self):
        logger.info("Inicializando base de datos")
        with self.get_connection() as con:
            cur = con.cursor()
            
            cur.execute("""
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER PRIMARY KEY
                )
            """)
            
            cur.execute("SELECT version FROM schema_version ORDER BY version DESC LIMIT 1")
            result = cur.fetchone()
            current_db_version = result[0] if result else 0
            
            if current_db_version < self.current_version:
                self._run_migrations(cur, current_db_version)
            
            # Tabla de expedientes (NUEVA)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS expedientes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    numero_expediente TEXT UNIQUE,
                    descripcion TEXT,
                    fecha_creacion TEXT,
                    estado TEXT DEFAULT 'ACTIVO',
                    created_at TEXT,
                    updated_at TEXT
                )
            """)
            
            # Migración automática si la tabla se creó con el script anterior
            cur.execute("PRAGMA table_info(expedientes)")
            columnas_exp = [col[1] for col in cur.fetchall()]
            if 'numero' in columnas_exp:
                cur.execute("ALTER TABLE expedientes RENAME COLUMN numero TO numero_expediente")
            if 'fecha' in columnas_exp:
                cur.execute("ALTER TABLE expedientes RENAME COLUMN fecha TO fecha_creacion")
            if 'descripcion' not in columnas_exp:
                cur.execute("ALTER TABLE expedientes ADD COLUMN descripcion TEXT")
            if 'estado' not in columnas_exp:
                cur.execute("ALTER TABLE expedientes ADD COLUMN estado TEXT DEFAULT 'ACTIVO'")
            if 'created_at' not in columnas_exp:
                cur.execute("ALTER TABLE expedientes ADD COLUMN created_at TEXT")
            if 'updated_at' not in columnas_exp:
                cur.execute("ALTER TABLE expedientes ADD COLUMN updated_at TEXT")

            cur.execute("""
                CREATE TABLE IF NOT EXISTS expediente_vehiculos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    expediente_id INTEGER,
                    interno INTEGER,
                    fecha_asignacion TEXT,
                    FOREIGN KEY (expediente_id) REFERENCES expedientes(id),
                    FOREIGN KEY (interno) REFERENCES vehiculos(interno),
                    UNIQUE(expediente_id, interno)
                )
            """)
            
            cur.execute("PRAGMA table_info(expediente_vehiculos)")
            columnas_ev = [col[1] for col in cur.fetchall()]
            if 'fecha_asignacion' not in columnas_ev:
                cur.execute("ALTER TABLE expediente_vehiculos ADD COLUMN fecha_asignacion TEXT")

            con.commit()
            logger.info(f"Base de datos inicializada. Versión: {self.current_version}")

    def _run_migrations(self, cursor, from_version):
        logger.info(f"Ejecutando migraciones desde versión {from_version}")
        
        if from_version < 1:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS vehiculos (
                    interno INTEGER PRIMARY KEY,
                    dominio TEXT,
                    marca TEXT,
                    modelo TEXT,
                    anio TEXT,
                    dependencia TEXT,
                    foto_path TEXT,
                    motor_path TEXT,
                    chasis_path TEXT,
                    docu_path TEXT,
                    inventario_pdf_path TEXT,
                    informe_pdf_path TEXT,
                    chasis_numero TEXT,
                    motor_numero TEXT,
                    created_at TEXT,
                    updated_at TEXT
                )
            """)
            cursor.execute("INSERT INTO schema_version (version) VALUES (1)")
        
        if from_version < 2:
            try:
                cursor.execute("ALTER TABLE vehiculos ADD COLUMN orden INTEGER")
            except sqlite3.OperationalError:
                pass
            cursor.execute("INSERT INTO schema_version (version) VALUES (2)")
        
        if from_version < 3:
            try:
                cursor.execute("ALTER TABLE vehiculos ADD COLUMN excluded INTEGER DEFAULT 0")
            except sqlite3.OperationalError:
                pass
            cursor.execute("INSERT INTO schema_version (version) VALUES (3)")
        
        if from_version < 4:
            columns = ["superintendencia", "direccion_general", "departamento", "memorando", "fecha_asignacion"]
            for col in columns:
                try:
                    cursor.execute(f"ALTER TABLE vehiculos ADD COLUMN {col} TEXT")
                except sqlite3.OperationalError:
                    pass
            cursor.execute("INSERT INTO schema_version (version) VALUES (4)")
        
        if from_version < 5:
            columns = ["provincia", "localidad", "tipo_combustible", "tipo_1", "tipo_2", "estado_patrimonial"]
            for col in columns:
                try:
                    cursor.execute(f"ALTER TABLE vehiculos ADD COLUMN {col} TEXT")
                except sqlite3.OperationalError:
                    pass
            cursor.execute("INSERT INTO schema_version (version) VALUES (5)")
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS verificaciones (
                interno INTEGER PRIMARY KEY,
                resultado TEXT,
                observacion TEXT,
                FOREIGN KEY (interno) REFERENCES vehiculos(interno)
            )
        """)

    def get_vehicle(self, interno):
        with self.get_connection() as con:
            cur = con.cursor()
            cur.execute("""
                SELECT interno, dominio, marca, modelo, anio, dependencia,
                       foto_path, motor_path, chasis_path, docu_path,
                       inventario_pdf_path, informe_pdf_path, chasis_numero, motor_numero,
                       excluded, superintendencia, direccion_general, departamento, 
                       memorando, fecha_asignacion, provincia, localidad, 
                       tipo_combustible, tipo_1, tipo_2
                FROM vehiculos WHERE interno = ?
            """, (interno,))
            return cur.fetchone()

    def get_all_vehicles_df(self):
        with self.get_connection() as con:
            df = pd.read_sql_query("""
                SELECT orden, interno, dominio, marca, modelo, anio, dependencia,
                       foto_path, motor_path, chasis_path, docu_path,
                       inventario_pdf_path, informe_pdf_path, chasis_numero, motor_numero,
                       excluded, superintendencia, direccion_general, departamento, 
                       memorando, fecha_asignacion, provincia, localidad, 
                       tipo_combustible, tipo_1, tipo_2
                FROM vehiculos ORDER BY COALESCE(orden, interno) ASC
            """, con)
            df = df.replace({'nan': '', 'None': '', None: ''})
            if 'anio' in df.columns:
                df['anio'] = df['anio'].astype(str).str.replace(r'\.0$', '', regex=True)
            return df

    def upsert_vehicle(self, data):
        with self.get_connection() as con:
            cur = con.cursor()
            now = datetime.now().isoformat(timespec="seconds")
            data.setdefault("created_at", now)
            data["updated_at"] = now
            
            interno = data.get("interno")
            if interno is None:
                logger.error("Se intentó insertar un vehículo sin 'interno'")
                return
            
            cur.execute("SELECT COUNT(*) FROM vehiculos WHERE interno=?", (interno,))
            exists = cur.fetchone()[0] > 0
            
            if exists:
                fields = [k for k in data.keys() if k != "interno"]
                placeholders = ", ".join([f"{f}=?" for f in fields])
                values = [data.get(f) for f in fields] + [interno]
                cur.execute(f"UPDATE vehiculos SET {placeholders} WHERE interno=?", values)
            else:
                fields = list(data.keys())
                values = [data.get(f) for f in fields]
                placeholders = ", ".join(['?'] * len(fields))
                cur.execute(f"INSERT INTO vehiculos ({', '.join(fields)}) VALUES ({placeholders})", values)
            
            con.commit()

    def delete_vehicle(self, interno):
        with self.get_connection() as con:
            cur = con.cursor()
            try:
                cur.execute("DELETE FROM vehiculos WHERE interno = ?", (interno,))
                con.commit()
                logger.info(f"Vehículo eliminado: interno {interno}")
                return True
            except Exception as e:
                con.rollback()
                logger.error(f"Error al eliminar vehículo {interno}: {e}")
                return False

    # =============================================================================
    # NUEVAS FUNCIONES PARA EXPEDIENTES
    # =============================================================================
    def get_all_expedientes_df(self):
        with self.get_connection() as con:
            df = pd.read_sql_query("""
                SELECT e.id, e.numero_expediente, e.descripcion, e.fecha_creacion, 
                       e.estado, COUNT(ev.interno) as cantidad_vehiculos
                FROM expedientes e
                LEFT JOIN expediente_vehiculos ev ON e.id = ev.expediente_id
                GROUP BY e.id
                ORDER BY e.fecha_creacion DESC
            """, con)
            return df

    def create_expediente(self, numero, descripcion):
        with self.get_connection() as con:
            cur = con.cursor()
            now = datetime.now().isoformat(timespec="seconds")
            try:
                cur.execute("""
                    INSERT INTO expedientes (numero_expediente, descripcion, fecha_creacion, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                """, (numero, descripcion, now, now, now))
                con.commit()
                return cur.lastrowid
            except sqlite3.IntegrityError:
                return None

    def add_vehiculo_to_expediente(self, expediente_id, interno):
        with self.get_connection() as con:
            cur = con.cursor()
            now = datetime.now().isoformat(timespec="seconds")
            try:
                cur.execute("""
                    INSERT INTO expediente_vehiculos (expediente_id, interno, fecha_asignacion)
                    VALUES (?, ?, ?)
                """, (expediente_id, interno, now))
                con.commit()
                return True
            except sqlite3.IntegrityError:
                return False

    def remove_vehiculo_from_expediente(self, expediente_id, interno):
        with self.get_connection() as con:
            cur = con.cursor()
            cur.execute("""
                DELETE FROM expediente_vehiculos 
                WHERE expediente_id = ? AND interno = ?
            """, (expediente_id, interno))
            con.commit()

    def get_vehiculos_by_expediente(self, expediente_id):
        with self.get_connection() as con:
            cur = con.cursor()
            cur.execute("""
                SELECT v.* FROM vehiculos v
                JOIN expediente_vehiculos ev ON v.interno = ev.interno
                WHERE ev.expediente_id = ?
            """, (expediente_id,))
            return cur.fetchall()

    def get_expedientes_by_vehiculo(self, interno):
        with self.get_connection() as con:
            cur = con.cursor()
            cur.execute("""
                SELECT e.* FROM expedientes e
                JOIN expediente_vehiculos ev ON e.id = ev.expediente_id
                WHERE ev.interno = ?
            """, (interno,))
            return cur.fetchall()

    def is_vehiculo_in_expediente(self, interno):
        with self.get_connection() as con:
            cur = con.cursor()
            cur.execute("""
                SELECT COUNT(*) FROM expediente_vehiculos WHERE interno = ?
            """, (interno,))
            return cur.fetchone()[0] > 0

    def obtener_internos_en_expedientes(self):
        with self.get_connection() as con:
            cur = con.cursor()
            cur.execute("SELECT interno FROM expediente_vehiculos")
            return {row[0] for row in cur.fetchall()}

    def update_expediente_estado(self, expediente_id, estado):
        with self.get_connection() as con:
            cur = con.cursor()
            now = datetime.now().isoformat(timespec="seconds")
            cur.execute("""
                UPDATE expedientes SET estado = ?, updated_at = ? WHERE id = ?
            """, (estado, now, expediente_id))
            con.commit()


db_manager = DatabaseManager()

# =============================================================================
# COMPONENTES UI REUTILIZABLES
# =============================================================================
class ModernStyle:
    """Estilos modernos para la aplicación"""
    
    # Colores del tema dark mode
    COLORS = {
        'bg_primary': '#1a1a2e',
        'bg_secondary': '#16213e',
        'bg_card': '#0f3460',
        'accent': '#e94560',
        'accent_hover': '#ff6b6b',
        'text_primary': '#ffffff',
        'text_secondary': '#a0a0a0',
        'success': '#00d26a',
        'warning': '#ffc107',
        'danger': '#dc3545',
        'info': '#17a2b8',
        'border': '#2a2a4a'
    }
    
    @staticmethod
    def apply_dark_theme(root):
        """Aplica tema oscuro a la ventana principal"""
        root.configure(bg=ModernStyle.COLORS['bg_primary'])
        
        style = ttk.Style(root)
        
        # Configurar estilos para ttkbootstrap si está disponible
        if USE_TTKBOOTSTRAP:
            root = ttkb.Style(theme='darkly')
        else:
            # Estilos custom para ttk estándar
            style.configure('TFrame', background=ModernStyle.COLORS['bg_primary'])
            style.configure('TLabel', background=ModernStyle.COLORS['bg_primary'], 
                          foreground=ModernStyle.COLORS['text_primary'],
                          font=('Segoe UI', 10))
            style.configure('TButton', font=('Segoe UI', 10, 'bold'),
                          padding=10)
            style.configure('Header.TLabel', font=('Segoe UI', 14, 'bold'),
                          foreground=ModernStyle.COLORS['text_primary'])
            style.configure('Sidebar.TButton', font=('Segoe UI', 11),
                          padding=(20, 15), anchor='w')
            style.configure('Card.TFrame', background=ModernStyle.COLORS['bg_card'],
                          relief='raised', borderwidth=1)
            style.configure('Treeview', background=ModernStyle.COLORS['bg_secondary'],
                          foreground=ModernStyle.COLORS['text_primary'],
                          fieldbackground=ModernStyle.COLORS['bg_secondary'],
                          rowheight=30)
            style.configure('Treeview.Heading', background=ModernStyle.COLORS['bg_card'],
                          foreground=ModernStyle.COLORS['text_primary'],
                          font=('Segoe UI', 10, 'bold'))
            style.map('Treeview', background=[('selected', ModernStyle.COLORS['accent'])])
            
            # Entry styles
            style.configure('TEntry', padding=10, font=('Segoe UI', 10))
            
            # Notebook styles
            style.configure('TNotebook', background=ModernStyle.COLORS['bg_primary'])
            style.configure('TNotebook.Tab', padding=(20, 10), font=('Segoe UI', 10))


class LoadingOverlay:
    """Overlay de carga para operaciones largas"""
    
    def __init__(self, parent, message="Procesando..."):
        self.parent = parent
        self.message = message
        self.top = None
        
    def show(self):
        self.top = Toplevel(self.parent)
        self.top.title("")
        self.top.resizable(False, False)
        self.top.configure(bg=ModernStyle.COLORS['bg_primary'])
        
        # Centrar en la ventana padre
        self.top.transient(self.parent)
        self.top.grab_set()
        
        x = self.parent.winfo_rootx() + self.parent.winfo_width() // 2 - 150
        y = self.parent.winfo_rooty() + self.parent.winfo_height() // 2 - 50
        self.top.geometry(f"300x100+{x}+{y}")
        
        # Label de mensaje
        lbl = ttk.Label(self.top, text=self.message, 
                       style='Header.TLabel',
                       background=ModernStyle.COLORS['bg_primary'])
        lbl.pack(pady=20)
        
        # Barra de progreso
        self.progress = ttk.Progressbar(self.top, mode='indeterminate', length=250)
        self.progress.pack(pady=10)
        self.progress.start(10)
        
    def hide(self):
        if self.top:
            self.progress.stop()
            self.top.destroy()
            self.top = None


class ModalDialog:
    """Diálogo modal moderno"""
    
    def __init__(self, parent, title, content_frame, on_confirm=None, on_cancel=None):
        self.parent = parent
        self.title = title
        self.content_frame = content_frame
        self.on_confirm = on_confirm
        self.on_cancel = on_cancel
        self.result = None
        self._create_dialog()
        
    def _create_dialog(self):
        self.top = Toplevel(self.parent)
        self.top.title(self.title)
        self.top.resizable(False, False)
        self.top.configure(bg=ModernStyle.COLORS['bg_primary'])
        
        # Centrar
        self.top.transient(self.parent)
        self.top.grab_set()
        
        x = self.parent.winfo_rootx() + self.parent.winfo_width() // 2 - 250
        y = self.parent.winfo_rooty() + self.parent.winfo_height() // 2 - 200
        self.top.geometry(f"500x400+{x}+{y}")
        
        # Contenido
        content_container = ttk.Frame(self.top, style='Card.TFrame', padding=20)
        content_container.pack(fill='both', expand=True, padx=20, pady=20)
        
        # Título
        title_lbl = ttk.Label(content_container, text=self.title, 
                             style='Header.TLabel',
                             background=ModernStyle.COLORS['bg_card'])
        title_lbl.pack(pady=(0, 20))
        
        # Frame de contenido personalizado
        self.content_frame.pack(in_=content_container, fill='both', expand=True)
        
        # Botones
        btn_frame = ttk.Frame(content_container, style='Card.TFrame')
        btn_frame.pack(fill='x', pady=(20, 0))
        
        ttk.Button(btn_frame, text="Cancelar", command=self._on_cancel,
                  style='TButton').pack(side='right', padx=5)
        ttk.Button(btn_frame, text="Confirmar", command=self._on_confirm,
                  style='Accent.TButton' if USE_TTKBOOTSTRAP else 'TButton').pack(side='right', padx=5)
        
    def _on_confirm(self):
        if self.on_confirm:
            self.result = self.on_confirm()
        if self.result is not False:
            self.top.destroy()
            
    def _on_cancel(self):
        if self.on_cancel:
            self.on_cancel()
        self.top.destroy()


# =============================================================================
# VISTAS PRINCIPALES
# =============================================================================
class Sidebar(ttk.Frame):
    """Panel lateral de navegación"""
    
    def __init__(self, parent, on_navigation):
        super().__init__(parent, style='Card.TFrame', width=250)
        self.pack_propagate(False)
        self.on_navigation = on_navigation
        
        self._create_navigation()
        
    def _create_navigation(self):
        # Logo / Título
        title_frame = ttk.Frame(self, style='Card.TFrame')
        title_frame.pack(fill='x', pady=30, padx=20)
        
        title_lbl = ttk.Label(title_frame, text="🚗 FLOTA 2026", 
                             style='Header.TLabel',
                             font=('Segoe UI', 18, 'bold'))
        title_lbl.pack()
        
        # Items de navegación
        nav_items = [
            ("📊", "Dashboard", "dashboard"),
            ("🚙", "Vehículos", "vehiculos"),
            ("📁", "Expedientes", "expedientes"),
            ("✅", "Verificaciones", "verificaciones"),
            ("📄", "Informes", "informes"),
            ("⚙️", "Configuración", "configuracion")
        ]
        
        self.nav_buttons = {}
        
        for icon, text, view_id in nav_items:
            btn = ttk.Button(
                self,
                text=f"{icon}  {text}",
                command=lambda v=view_id: self.on_navigation(v),
                style='Sidebar.TButton' if USE_TTKBOOTSTRAP else 'TButton'
            )
            btn.pack(fill='x', padx=10, pady=5)
            self.nav_buttons[view_id] = btn
        
        # Separador
        ttk.Separator(self, orient='horizontal').pack(fill='x', padx=20, pady=20)
        
        # Info de usuario
        user_frame = ttk.Frame(self, style='Card.TFrame')
        user_frame.pack(side='bottom', fill='x', padx=20, pady=20)
        
        user_lbl = ttk.Label(user_frame, text="👤 Usuario Admin",
                            style='TLabel',
                            font=('Segoe UI', 9))
        user_lbl.pack()
        
    def set_active_view(self, view_id):
        """Resalta el botón activo"""
        for vid, btn in self.nav_buttons.items():
            if USE_TTKBOOTSTRAP:
                if vid == view_id:
                    btn.configure(bootstyle='info')
                else:
                    btn.configure(bootstyle='outline')
            else:
                if vid == view_id:
                    btn.configure(style='Accent.TButton')
                else:
                    btn.configure(style='TButton')


class DashboardView(ttk.Frame):
    """Vista principal del dashboard"""
    
    def __init__(self, parent, app):
        super().__init__(parent, style='TFrame')
        self.app = app
        
        self._create_dashboard()
        
    def _create_dashboard(self):
        # Header
        header_frame = ttk.Frame(self, style='TFrame')
        header_frame.pack(fill='x', padx=30, pady=20)
        
        title_lbl = ttk.Label(header_frame, text="📊 Dashboard", 
                             style='Header.TLabel')
        title_lbl.pack(side='left')
        
        # Stats cards
        stats_frame = ttk.Frame(self, style='TFrame')
        stats_frame.pack(fill='x', padx=30, pady=20)
        
        self.stats_cards = {}
        stats_config = [
            ("total", "🚙 Total Vehículos", "#0f3460"),
            ("completos", "✅ Completos", "#00d26a"),
            ("incompletos", "⚠️ Incompletos", "#ffc107"),
            ("expedientes", "📁 Expedientes", "#17a2b8")
        ]
        
        for i, (key, label, color) in enumerate(stats_config):
            card = self._create_stat_card(stats_frame, label, "0", color)
            card.grid(row=0, column=i, padx=10, sticky='ew')
            stats_frame.columnconfigure(i, weight=1)
            self.stats_cards[key] = card
        
        # Recent activity
        activity_frame = ttk.Frame(self, style='Card.TFrame', padding=20)
        activity_frame.pack(fill='both', expand=True, padx=30, pady=20)
        
        activity_title = ttk.Label(activity_frame, text="📈 Actividad Reciente",
                                  style='Header.TLabel')
        activity_title.pack(anchor='w', pady=(0, 15))
        
        self.activity_text = tk.Text(activity_frame, height=10, 
                                    bg=ModernStyle.COLORS['bg_secondary'],
                                    fg=ModernStyle.COLORS['text_primary'],
                                    font=('Segoe UI', 10),
                                    relief='flat',
                                    wrap='word')
        self.activity_text.pack(fill='both', expand=True)
        
        # Actualizar stats
        self._update_stats()
        
    def _create_stat_card(self, parent, label, value, color):
        card = ttk.Frame(parent, style='Card.TFrame', padding=20)
        
        value_lbl = ttk.Label(card, text=value, 
                             font=('Segoe UI', 28, 'bold'),
                             foreground=color,
                             background=ModernStyle.COLORS['bg_card'])
        value_lbl.pack()
        
        label_lbl = ttk.Label(card, text=label,
                             font=('Segoe UI', 11),
                             background=ModernStyle.COLORS['bg_card'])
        label_lbl.pack()
        
        return card
        
    def _update_stats(self):
        """Actualiza las estadísticas del dashboard"""
        try:
            df = self.app.db_manager.get_all_vehicles_df()
            total = len(df)
            
            completos = len(df[
                (df['foto_path'] != '') & 
                (df['motor_path'] != '') & 
                (df['chasis_path'] != '') & 
                (df['docu_path'] != '')
            ])
            
            incompletos = total - completos
            
            expedientes_df = self.app.db_manager.get_all_expedientes_df()
            total_expedientes = len(expedientes_df)
            
            self.stats_cards['total'].winfo_children()[0].configure(text=str(total))
            self.stats_cards['completos'].winfo_children()[0].configure(text=str(completos))
            self.stats_cards['incompletos'].winfo_children()[0].configure(text=str(incompletos))
            self.stats_cards['expedientes'].winfo_children()[0].configure(text=str(total_expedientes))
            
        except Exception as e:
            logger.error(f"Error actualizando stats: {e}")


class VehiculosView(ttk.Frame):
    """Vista de gestión de vehículos"""
    
    def __init__(self, parent, app):
        super().__init__(parent, style='TFrame')
        self.app = app
        self.current_interno = None
        self.current_image_paths = {}
        self.aclaraciones_por_interno = {}
        self.entries = {}
        self.preview_labels = {}
        self.preview_img_refs = {}
        self.aclaraciones_widgets = {}
        
        self._create_view()
        
    def _create_view(self):
        # Header con búsqueda
        header_frame = ttk.Frame(self, style='TFrame')
        header_frame.pack(fill='x', padx=30, pady=20)
        
        title_lbl = ttk.Label(header_frame, text="🚙 Gestión de Vehículos", 
                             style='Header.TLabel')
        title_lbl.pack(side='left')
        
        # Barra de búsqueda
        search_frame = ttk.Frame(header_frame, style='TFrame')
        search_frame.pack(side='right')
        
        search_lbl = ttk.Label(search_frame, text="🔎 Buscar:", 
                              style='TLabel')
        search_lbl.pack(side='left', padx=(0, 10))
        
        self.search_var = tk.StringVar()
        self.search_var.trace_add('write', self._filter_tree)
        search_entry = ttk.Entry(search_frame, textvariable=self.search_var, width=30)
        search_entry.pack(side='left')
        
        # Contenedor principal (tabla + formulario)
        main_container = ttk.Panedwindow(self, orient='horizontal')
        main_container.pack(fill='both', expand=True, padx=30, pady=10)
        
        # Panel izquierdo - Tabla
        table_frame = ttk.Frame(main_container, style='Card.TFrame')
        main_container.add(table_frame, weight=2)
        
        self._create_treeview(table_frame)
        
        # Panel derecho - Formulario
        form_frame = ttk.Frame(main_container, style='Card.TFrame', padding=20)
        main_container.add(form_frame, weight=1)
        
        self._create_formulario(form_frame)
        
        # Cargar datos iniciales
        self._refresh_tree()
        
    def _create_treeview(self, parent):
        # Toolbar
        toolbar_frame = ttk.Frame(parent, style='Card.TFrame')
        toolbar_frame.pack(fill='x', padx=10, pady=10)
        
        ttk.Button(toolbar_frame, text="➕ Nuevo", 
                  command=self._nuevo_vehiculo).pack(side='left', padx=5)
        ttk.Button(toolbar_frame, text="📥 Importar Excel", 
                  command=self.app.importar_excel).pack(side='left', padx=5)
        ttk.Button(toolbar_frame, text="🗑️ Eliminar", 
                  command=self._eliminar_vehiculo).pack(side='left', padx=5)
        
        # Treeview
        tree_container = ttk.Frame(parent, style='Card.TFrame')
        tree_container.pack(fill='both', expand=True, padx=10, pady=10)
        
        columns = ("orden", "interno", "dominio", "marca", "modelo", "anio", "estado")
        self.tree = ttk.Treeview(tree_container, columns=columns, show='headings')
        
        vsb = ttk.Scrollbar(tree_container, orient='vertical', command=self.tree.yview)
        hsb = ttk.Scrollbar(tree_container, orient='horizontal', command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        
        self.tree.pack(side='left', fill='both', expand=True)
        vsb.pack(side='right', fill='y')
        hsb.pack(side='bottom', fill='x')
        
        # Configurar columnas
        column_widths = {
            "orden": 60, "interno": 80, "dominio": 120, 
            "marca": 120, "modelo": 120, "anio": 60, "estado": 100
        }
        
        for col in columns:
            self.tree.heading(col, text=col.upper())
            self.tree.column(col, width=column_widths.get(col, 100), anchor='center')
        
        self.tree.bind('<<TreeviewSelect>>', self.on_row_select)
        self.tree.bind('<Button-3>', self._show_context_menu)
        
    def _create_formulario(self, parent):
        # Título
        title_lbl = ttk.Label(parent, text="📝 Datos del Vehículo", 
                             style='Header.TLabel')
        title_lbl.pack(pady=(0, 20))
        
        # Campos del formulario
        campos = [
            ("interno", "Interno:", 20),
            ("dominio", "Dominio:", 20),
            ("marca", "Marca:", 20),
            ("modelo", "Modelo:", 20),
            ("anio", "Año:", 10),
            ("dependencia", "Dependencia:", 30),
            ("chasis_numero", "N° Chasis:", 25),
            ("motor_numero", "N° Motor:", 25),
        ]
        
        for key, label, width in campos:
            field_frame = ttk.Frame(parent, style='Card.TFrame')
            field_frame.pack(fill='x', pady=5)
            
            lbl = ttk.Label(field_frame, text=label, width=15, anchor='e')
            lbl.pack(side='left', padx=(0, 10))
            
            entry = ttk.Entry(field_frame, width=width)
            entry.pack(side='left', fill='x', expand=True)
            self.entries[key] = entry
        
        # Botones de imágenes
        img_frame = ttk.LabelFrame(parent, text="📷 Imágenes", padding=10)
        img_frame.pack(fill='x', pady=20)
        
        img_buttons = [
            ("Cargar Foto", "foto"),
            ("Cargar Motor", "motor"),
            ("Cargar Chasis", "chasis"),
            ("Cargar Docu", "docu")
        ]
        
        for text, tipo in img_buttons:
            btn = ttk.Button(img_frame, text=text,
                           command=lambda t=tipo: self.cargar_imagen(t))
            btn.pack(side='left', padx=5, pady=5)
        
        # Previsualizaciones
        preview_frame = ttk.Frame(parent, style='Card.TFrame')
        preview_frame.pack(fill='x', pady=10)
        
        for i, tipo in enumerate(["foto", "motor", "chasis", "docu"]):
            frame = ttk.LabelFrame(preview_frame, text=tipo.upper())
            frame.grid(row=0, column=i, padx=5, pady=5, sticky='n')
            
            label = ttk.Label(frame, text="Sin imagen", cursor="hand2")
            label.pack(padx=5, pady=5)
            label.bind('<Button-1>', lambda e, t=tipo: self._show_large_image(self.current_image_paths.get(t)))
            
            self.preview_labels[tipo] = label
        
        # Botón guardar
        btn_guardar = ttk.Button(parent, text="💾 Guardar Vehículo",
                                command=self.guardar_vehiculo)
        btn_guardar.pack(pady=20)
        
        # Pestaña de aclaraciones
        acl_frame = ttk.LabelFrame(parent, text="📝 Aclaraciones", padding=10)
        acl_frame.pack(fill='both', expand=True, pady=10)
        
        self.aclaraciones_widgets = {}
        tipos = [("foto", "Foto Vehículo"), ("motor", "Motor"), 
                 ("chasis", "Chasis"), ("docu", "Documentación")]
        
        for tipo, descripcion in tipos:
            item_frame = ttk.Frame(acl_frame)
            item_frame.pack(fill='x', pady=5)
            
            var = tk.BooleanVar()
            cb = ttk.Checkbutton(item_frame, text=descripcion, variable=var)
            cb.pack(side='left', padx=(0, 10))
            
            text_widget = tk.Text(item_frame, height=3, width=30, wrap='word')
            text_widget.pack(side='left', fill='x', expand=True)
            
            self.aclaraciones_widgets[tipo] = {'var': var, 'text': text_widget}
        
        btn_aclaraciones = ttk.Button(parent, text="💾 Guardar Aclaraciones",
                                     command=self.guardar_aclaraciones)
        btn_aclaraciones.pack(pady=10)
        
    def _refresh_tree(self):
        """Carga todos los vehículos desde la BD"""
        self.all_vehicles_df = self.app.db_manager.get_all_vehicles_df()
        self._filter_tree()
        
    def _filter_tree(self, *args):
        """Filtra el Treeview según búsqueda"""
        search_term = self.search_var.get().lower().strip()
        self.tree.delete(*self.tree.get_children())
        
        if self.all_vehicles_df.empty:
            return
        
        for _, row in self.all_vehicles_df.iterrows():
            row_as_string = ' '.join(row.fillna('').astype(str).str.lower())
            
            if not search_term or search_term in row_as_string:
                # Determinar estado
                estado = "✅ Completo"
                if not all([row['foto_path'], row['motor_path'], 
                           row['chasis_path'], row['docu_path']]):
                    estado = "⚠️ Incompleto"
                if row.get('excluded', 0):
                    estado = "🚫 Excluido"
                
                self.tree.insert('', 'end', values=(
                    row['orden'], row['interno'], row['dominio'],
                    row['marca'], row['modelo'], row['anio'], estado
                ))
    
    def on_row_select(self, event):
        """Maneja selección de vehículo"""
        sel = self.tree.selection()
        if not sel:
            self.current_interno = None
            self._clear_form()
            return
        
        values = self.tree.item(sel[0], 'values')
        if len(values) < 2:
            return
        
        try:
            interno = int(float(values[1]))
        except (ValueError, TypeError):
            return
        
        self.current_interno = interno
        row = self.app.db_manager.get_vehicle(interno)
        
        if not row:
            self._clear_form()
            return
        
        # Cargar datos en formulario
        campos = [("interno", 0), ("dominio", 1), ("marca", 2), ("modelo", 3),
                  ("anio", 4), ("dependencia", 5), ("chasis_numero", 12), ("motor_numero", 13)]
        
        for key, idx in campos:
            self.entries[key].delete(0, tk.END)
            if row[idx] is not None:
                self.entries[key].insert(0, str(row[idx]))
        
        # Cargar rutas de imágenes
        self.current_image_paths = {
            "foto": row[6], "motor": row[7],
            "chasis": row[8], "docu": row[9]
        }
        
        # Mostrar previews
        for tipo, path in self.current_image_paths.items():
            self._mostrar_imagen_preview(tipo, path)
        
        # Cargar aclaraciones
        self._cargar_aclaraciones(interno)
        
    def _clear_form(self):
        """Limpia el formulario"""
        for entry in self.entries.values():
            entry.delete(0, tk.END)
        for tipo, label in self.preview_labels.items():
            label.config(text="Sin imagen", image="")
        for tipo, widgets in self.aclaraciones_widgets.items():
            widgets['var'].set(False)
            widgets['text'].delete('1.0', tk.END)
            
    def _mostrar_imagen_preview(self, key, path_relativa):
        """Muestra preview de imagen"""
        label_widget = self.preview_labels[key]
        
        if key in self.preview_img_refs:
            del self.preview_img_refs[key]
        
        if not path_relativa:
            label_widget.config(text="Sin imagen", image="")
            return
        
        abs_path = config.get_absolute_path(path_relativa)
        
        if not abs_path or not abs_path.exists():
            label_widget.config(text="❌ No encontrada", image="")
            return
        
        if str(abs_path).lower().endswith(('.pdf', '.doc', '.docx')):
            label_widget.config(text="📄 Documento", image="")
            return
        
        try:
            max_width = int(config.config['IMAGE']['preview_width'])
            max_height = int(config.config['IMAGE']['preview_height'])
            
            img = Image.open(abs_path)
            width, height = img.size
            ratio = min(max_width/width, max_height/height)
            new_width = max(1, int(width * ratio))
            new_height = max(1, int(height * ratio))
            
            img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
            self.preview_img_refs[key] = ImageTk.PhotoImage(img)
            label_widget.config(image=self.preview_img_refs[key], text="")
            
        except Exception as e:
            label_widget.config(text=f"❌ Error", image="")
            logger.error(f"Error cargando preview: {e}")
            
    def _cargar_aclaraciones(self, interno):
        """Carga aclaraciones guardadas"""
        vehiculo_aclaraciones = self.aclaraciones_por_interno.get(interno, {})
        
        for tipo, widgets in self.aclaraciones_widgets.items():
            data = vehiculo_aclaraciones.get(tipo, {})
            is_active = data.get('active', False)
            texto = data.get('text') or DEFAULT_ACLARACIONES.get(tipo, '')
            
            widgets['var'].set(is_active)
            widgets['text'].delete('1.0', tk.END)
            widgets['text'].insert('1.0', texto)
            
    def guardar_vehiculo(self):
        """Guarda datos del vehículo"""
        try:
            data = {key: entry.get() for key, entry in self.entries.items()}
            
            if not data['interno']:
                messagebox.showwarning("Atención", "El interno es requerido")
                return
                
            interno_norm = limpiar_interno_val(data['interno'])
            if interno_norm is None:
                messagebox.showwarning("Error", "El interno debe ser un número")
                return
                
            data['interno'] = int(interno_norm)
            self.app.db_manager.upsert_vehicle(data)
            self._refresh_tree()
            
            messagebox.showinfo("Éxito", "Vehículo guardado correctamente")
            
        except Exception as e:
            logger.error(f"Error guardando vehículo: {e}")
            messagebox.showerror("Error", f"Error guardando: {str(e)}")
            
    def guardar_aclaraciones(self):
        """Guarda aclaraciones"""
        if self.current_interno is None:
            messagebox.showwarning("Atención", "Seleccioná un vehículo primero")
            return
        
        if self.current_interno not in self.aclaraciones_por_interno:
            self.aclaraciones_por_interno[self.current_interno] = {}
        
        for tipo, widgets in self.aclaraciones_widgets.items():
            is_active = widgets['var'].get()
            custom_text = widgets['text'].get('1.0', tk.END).strip()
            
            self.aclaraciones_por_interno[self.current_interno][tipo] = {
                'active': is_active,
                'text': custom_text
            }
        
        # Guardar en archivo JSON
        self._save_aclaraciones()
        messagebox.showinfo("Éxito", f"Aclaraciones guardadas para móvil {self.current_interno}")
        
    def _save_aclaraciones(self):
        """Guarda aclaraciones en archivo"""
        aclaraciones_file = config.get_path('base_dir') / 'aclaraciones_guardadas.json'
        try:
            data_a_guardar = {str(k): v for k, v in self.aclaraciones_por_interno.items()}
            with open(aclaraciones_file, 'w', encoding='utf-8') as f:
                json.dump(data_a_guardar, f, indent=4, ensure_ascii=False)
            logger.info(f"Aclaraciones guardadas en {aclaraciones_file}")
        except Exception as e:
            logger.error(f"Error guardando aclaraciones: {e}")
            
    def cargar_imagen(self, tipo):
        """Carga imagen para el vehículo"""
        if not self.entries['interno'].get():
            messagebox.showwarning("Atención", "Ingresá o seleccioná un vehículo primero")
            return
        
        path = filedialog.askopenfilename(
            title=f"Seleccionar imagen de {tipo}",
            filetypes=[("Imágenes", "*.jpg *.jpeg *.png"), ("Todos", "*.*")]
        )
        
        if not path:
            return
        
        try:
            interno = limpiar_interno_val(self.entries['interno'].get())
            if interno is None:
                messagebox.showwarning("Atención", "Interno inválido")
                return
            
            dominio = self.entries['dominio'].get()
            marca = self.entries['marca'].get()
            modelo = self.entries['modelo'].get()
            
            carpeta = PathManager.get_vehicle_folder(interno, dominio, marca, modelo)
            nombre_base = f"{tipo}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
            
            destino_absoluto = PathManager.copy_and_process_file(path, carpeta, nombre_base)
            destino_relativo = config.get_relative_path(destino_absoluto)
            
            data = {
                'interno': int(interno),
                f'{tipo}_path': str(destino_relativo),
                'dominio': dominio, 'marca': marca, 'modelo': modelo,
                'anio': self.entries['anio'].get(),
                'dependencia': self.entries['dependencia'].get(),
                'chasis_numero': self.entries['chasis_numero'].get(),
                'motor_numero': self.entries['motor_numero'].get()
            }
            
            self.app.db_manager.upsert_vehicle(data)
            self._refresh_tree()
            self.on_row_select(None)
            
            messagebox.showinfo("Éxito", f"Imagen de {tipo} guardada")
            
        except Exception as e:
            logger.error(f"Error cargando imagen: {e}")
            messagebox.showerror("Error", str(e))
            
    def _nuevo_vehiculo(self):
        """Prepara formulario para nuevo vehículo"""
        self._clear_form()
        self.current_interno = None
        
    def _eliminar_vehiculo(self):
        """Elimina vehículo seleccionado"""
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("Atención", "Seleccioná un vehículo primero")
            return
        
        valores = self.tree.item(sel[0], 'values')
        interno = int(valores[1])
        dominio = valores[2]
        
        confirmar = messagebox.askyesno(
            "Confirmar Eliminación",
            f"¿Estás seguro de eliminar el vehículo?\n\nInterno: {interno}\nDominio: {dominio}"
        )
        
        if confirmar:
            if self.app.db_manager.delete_vehicle(interno):
                messagebox.showinfo("Éxito", f"Vehículo {interno} eliminado")
                self._refresh_tree()
            else:
                messagebox.showerror("Error", "No se pudo eliminar el vehículo")
                
    def _show_context_menu(self, event):
        """Muestra menú contextual"""
        item = self.tree.identify_row(event.y)
        if not item:
            return
        
        self.tree.selection_set(item)
        self.on_row_select(None)
        
        try:
            interno = int(self.tree.item(item, 'values')[1])
            row = self.app.db_manager.get_vehicle(interno)
            
            if not row:
                return
            
            is_excluded = bool(row[14]) if len(row) > 14 and row[14] else False
            
            menu = tk.Menu(self, tearoff=0)
            
            if is_excluded:
                menu.add_command(label="✅ Incluir en IFGRA",
                               command=lambda: self._toggle_exclusion(interno, False))
            else:
                menu.add_command(label="🚫 Excluir de IFGRA",
                               command=lambda: self._toggle_exclusion(interno, True))
            
            menu.post(event.x_root, event.y_root)
            
        except Exception as e:
            logger.error(f"Error mostrando menú: {e}")
            
    def _toggle_exclusion(self, interno, exclude):
        """Cambia estado excluded"""
        self.app.db_manager.upsert_vehicle({'interno': interno, 'excluded': 1 if exclude else 0})
        self._refresh_tree()
        
    def _show_large_image(self, path_relativa):
        """Abre visor de imagen grande"""
        if not path_relativa:
            return
        
        abs_path = config.get_absolute_path(path_relativa)
        if not abs_path or not abs_path.exists():
            messagebox.showerror("Error", f"No se encontró: {path_relativa}")
            return
        
        try:
            os.startfile(abs_path)
        except Exception:
            messagebox.showerror("Error", "No se pudo abrir el archivo")


class ExpedientesView(ttk.Frame):
    """Vista de gestión de expedientes (NUEVA)"""
    
    def __init__(self, parent, app):
        super().__init__(parent, style='TFrame')
        self.app = app
        self.current_expediente_id = None
        
        self._create_view()
        
    def _create_view(self):
        # Header
        header_frame = ttk.Frame(self, style='TFrame')
        header_frame.pack(fill='x', padx=30, pady=20)
        
        title_lbl = ttk.Label(header_frame, text="📁 Gestión de Expedientes", 
                             style='Header.TLabel')
        title_lbl.pack(side='left')
        
        ttk.Button(header_frame, text="➕ Nuevo Expediente",
                  command=self._nuevo_expediente).pack(side='right', padx=5)
        
        # Contenedor principal
        main_container = ttk.Panedwindow(self, orient='horizontal')
        main_container.pack(fill='both', expand=True, padx=30, pady=10)
        
        # Panel izquierdo - Lista de expedientes
        list_frame = ttk.Frame(main_container, style='Card.TFrame')
        main_container.add(list_frame, weight=1)
        
        self._create_expedientes_list(list_frame)
        
        # Panel derecho - Detalle
        detail_frame = ttk.Frame(main_container, style='Card.TFrame', padding=20)
        main_container.add(detail_frame, weight=2)
        
        self._create_expediente_detail(detail_frame)
        
        # Cargar datos
        self._refresh_expedientes()
        
    def _create_expedientes_list(self, parent):
        # Toolbar
        toolbar_frame = ttk.Frame(parent, style='Card.TFrame')
        toolbar_frame.pack(fill='x', padx=10, pady=10)
        
        search_lbl = ttk.Label(toolbar_frame, text="🔎 Buscar:")
        search_lbl.pack(side='left', padx=(0, 10))
        
        self.search_var = tk.StringVar()
        self.search_var.trace_add('write', self._filter_expedientes)
        search_entry = ttk.Entry(toolbar_frame, textvariable=self.search_var, width=20)
        search_entry.pack(side='left')
        
        # Treeview
        tree_container = ttk.Frame(parent, style='Card.TFrame')
        tree_container.pack(fill='both', expand=True, padx=10, pady=10)
        
        columns = ("id", "numero", "descripcion", "fecha", "estado", "vehiculos")
        self.expedientes_tree = ttk.Treeview(tree_container, columns=columns, show='headings')
        
        vsb = ttk.Scrollbar(tree_container, orient='vertical', command=self.expedientes_tree.yview)
        self.expedientes_tree.configure(yscrollcommand=vsb.set)
        
        self.expedientes_tree.pack(side='left', fill='both', expand=True)
        vsb.pack(side='right', fill='y')
        
        # Configurar columnas
        column_config = {
            "id": ("ID", 50),
            "numero": ("N° Expediente", 120),
            "descripcion": ("Descripción", 200),
            "fecha": ("Fecha", 100),
            "estado": ("Estado", 80),
            "vehiculos": ("Vehículos", 80)
        }
        
        for col, (label, width) in column_config.items():
            self.expedientes_tree.heading(col, text=label)
            self.expedientes_tree.column(col, width=width, anchor='center')
        
        self.expedientes_tree.bind('<<TreeviewSelect>>', self.on_expediente_select)
        
    def _create_expediente_detail(self, parent):
        # Título
        self.detail_title = ttk.Label(parent, text="📋 Detalle del Expediente", 
                                     style='Header.TLabel')
        self.detail_title.pack(pady=(0, 20))
        
        # Info del expediente
        info_frame = ttk.LabelFrame(parent, text="Información", padding=15)
        info_frame.pack(fill='x', pady=10)
        
        self.info_labels = {}
        info_fields = [
            ("numero", "N° Expediente:"),
            ("descripcion", "Descripción:"),
            ("fecha", "Fecha Creación:"),
            ("estado", "Estado:")
        ]
        
        for key, label in info_fields:
            field_frame = ttk.Frame(info_frame)
            field_frame.pack(fill='x', pady=5)
            
            lbl = ttk.Label(field_frame, text=label, width=15, anchor='e')
            lbl.pack(side='left', padx=(0, 10))
            
            value_lbl = ttk.Label(field_frame, text="-", width=40, anchor='w')
            value_lbl.pack(side='left')
            self.info_labels[key] = value_lbl
        
        # Vehículos asignados
        vehiculos_frame = ttk.LabelFrame(parent, text="🚙 Vehículos Asignados", padding=15)
        vehiculos_frame.pack(fill='both', expand=True, pady=10)
        
        # Toolbar de vehículos
        veh_toolbar = ttk.Frame(vehiculos_frame)
        veh_toolbar.pack(fill='x', pady=(0, 10))
        
        ttk.Button(veh_toolbar, text="➕ Agregar Vehículo",
                  command=self._agregar_vehiculo).pack(side='left', padx=5)
        ttk.Button(veh_toolbar, text="🗑️ Quitar Selección",
                  command=self._quitar_vehiculo).pack(side='left', padx=5)
        
        # Treeview de vehículos
        veh_tree_container = ttk.Frame(vehiculos_frame)
        veh_tree_container.pack(fill='both', expand=True)
        
        veh_columns = ("interno", "dominio", "marca", "modelo")
        self.vehiculos_tree = ttk.Treeview(veh_tree_container, columns=veh_columns, show='headings')
        
        vsb = ttk.Scrollbar(veh_tree_container, orient='vertical', command=self.vehiculos_tree.yview)
        self.vehiculos_tree.configure(yscrollcommand=vsb.set)
        
        self.vehiculos_tree.pack(side='left', fill='both', expand=True)
        vsb.pack(side='right', fill='y')
        
        for col in veh_columns:
            self.vehiculos_tree.heading(col, text=col.upper())
            self.vehiculos_tree.column(col, width=100, anchor='center')
        
        # Acciones
        actions_frame = ttk.Frame(parent, style='Card.TFrame')
        actions_frame.pack(fill='x', pady=20)
        
        ttk.Button(actions_frame, text="📄 Generar Informe",
                  command=self._generar_informe).pack(side='left', padx=5)
        ttk.Button(actions_frame, text="✅ Cerrar Expediente",
                  command=self._cerrar_expediente).pack(side='left', padx=5)
        ttk.Button(actions_frame, text="🗑️ Eliminar Expediente",
                  command=self._eliminar_expediente).pack(side='right', padx=5)
        
        # Estado inicial (sin selección)
        self._clear_detail()
        
    def _refresh_expedientes(self):
        """Carga expedientes desde BD"""
        self.all_expedientes_df = self.app.db_manager.get_all_expedientes_df()
        self._filter_expedientes()
        
    def _filter_expedientes(self, *args):
        """Filtra expedientes"""
        search_term = self.search_var.get().lower().strip()
        self.expedientes_tree.delete(*self.expedientes_tree.get_children())
        
        if self.all_expedientes_df.empty:
            return
        
        for _, row in self.all_expedientes_df.iterrows():
            if not search_term or search_term in str(row.values).lower():
                self.expedientes_tree.insert('', 'end', values=(
                    row['id'], row['numero_expediente'], row['descripcion'],
                    row['fecha_creacion'][:10] if row['fecha_creacion'] else '-',
                    row['estado'], row['cantidad_vehiculos']
                ))
                
    def on_expediente_select(self, event):
        """Maneja selección de expediente"""
        sel = self.expedientes_tree.selection()
        if not sel:
            self.current_expediente_id = None
            self._clear_detail()
            return
        
        values = self.expedientes_tree.item(sel[0], 'values')
        self.current_expediente_id = int(values[0])
        
        # Cargar info del expediente
        for _, row in self.all_expedientes_df.iterrows():
            if row['id'] == self.current_expediente_id:
                self.info_labels['numero'].configure(text=row['numero_expediente'])
                self.info_labels['descripcion'].configure(text=row['descripcion'] or '-')
                self.info_labels['fecha'].configure(text=row['fecha_creacion'][:10] if row['fecha_creacion'] else '-')
                self.info_labels['estado'].configure(text=row['estado'])
                break
        
        # Cargar vehículos
        self._cargar_vehiculos_expediente()
        
    def _clear_detail(self):
        """Limpia panel de detalle"""
        self.detail_title.configure(text="📋 Detalle del Expediente")
        for lbl in self.info_labels.values():
            lbl.configure(text="-")
        self.vehiculos_tree.delete(*self.vehiculos_tree.get_children())
        
    def _cargar_vehiculos_expediente(self):
        """Carga vehículos del expediente"""
        self.vehiculos_tree.delete(*self.vehiculos_tree.get_children())
        
        vehiculos = self.app.db_manager.get_vehiculos_by_expediente(self.current_expediente_id)
        
        for veh in vehiculos:
            self.vehiculos_tree.insert('', 'end', values=(
                veh[0],  # interno
                veh[1] or '-',  # dominio
                veh[2] or '-',  # marca
                veh[3] or '-'   # modelo
            ))
            
    def _nuevo_expediente(self):
        """Crea nuevo expediente"""
        dialog = Toplevel(self)
        dialog.title("Nuevo Expediente")
        dialog.geometry("400x300")
        dialog.transient(self)
        dialog.grab_set()
        
        dialog.configure(bg=ModernStyle.COLORS['bg_primary'])
        
        # Contenido
        content_frame = ttk.Frame(dialog, padding=20)
        content_frame.pack(fill='both', expand=True)
        
        ttk.Label(content_frame, text="📁 Crear Nuevo Expediente",
                 style='Header.TLabel').pack(pady=(0, 20))
        
        # Campos
        fields_frame = ttk.Frame(content_frame)
        fields_frame.pack(fill='x', pady=10)
        
        ttk.Label(fields_frame, text="N° Expediente:").pack(anchor='w')
        numero_entry = ttk.Entry(fields_frame, width=40)
        numero_entry.pack(fill='x', pady=5)
        
        ttk.Label(fields_frame, text="Descripción:").pack(anchor='w')
        desc_text = tk.Text(fields_frame, height=5, width=40)
        desc_text.pack(fill='x', pady=5)
        
        # Botones
        btn_frame = ttk.Frame(content_frame)
        btn_frame.pack(pady=20)
        
        def crear():
            numero = numero_entry.get().strip()
            descripcion = desc_text.get('1.0', tk.END).strip()
            
            if not numero:
                messagebox.showwarning("Atención", "El número de expediente es requerido")
                return
            
            expediente_id = self.app.db_manager.create_expediente(numero, descripcion)
            
            if expediente_id:
                messagebox.showinfo("Éxito", "Expediente creado correctamente")
                self._refresh_expedientes()
                dialog.destroy()
            else:
                messagebox.showerror("Error", "El número de expediente ya existe")
        
        ttk.Button(btn_frame, text="Cancelar", command=dialog.destroy).pack(side='right', padx=5)
        ttk.Button(btn_frame, text="Crear", command=crear).pack(side='right', padx=5)
        
    def _agregar_vehiculo(self):
        """Agrega vehículo al expediente"""
        if not self.current_expediente_id:
            messagebox.showwarning("Atención", "Seleccioná un expediente primero")
            return
        
        # Abrir ventana de selección de vehículos
        dialog = Toplevel(self)
        dialog.title("Agregar Vehículo")
        dialog.geometry("600x400")
        dialog.transient(self)
        dialog.grab_set()
        
        dialog.configure(bg=ModernStyle.COLORS['bg_primary'])
        
        content_frame = ttk.Frame(dialog, padding=20)
        content_frame.pack(fill='both', expand=True)
        
        ttk.Label(content_frame, text="🚙 Seleccionar Vehículo",
                 style='Header.TLabel').pack(pady=(0, 20))
        
        # Buscar vehículo
        search_frame = ttk.Frame(content_frame)
        search_frame.pack(fill='x', pady=10)
        
        ttk.Label(search_frame, text="🔎 Buscar por Interno o Dominio:").pack(side='left')
        search_var = tk.StringVar()
        search_entry = ttk.Entry(search_frame, textvariable=search_var, width=30)
        search_entry.pack(side='left', padx=10)
        
        # Lista de vehículos disponibles
        tree_container = ttk.Frame(content_frame)
        tree_container.pack(fill='both', expand=True, pady=10)
        
        columns = ("interno", "dominio", "marca", "modelo", "estado")
        veh_tree = ttk.Treeview(tree_container, columns=columns, show='headings')
        
        vsb = ttk.Scrollbar(tree_container, orient='vertical', command=veh_tree.yview)
        veh_tree.configure(yscrollcommand=vsb.set)
        
        veh_tree.pack(side='left', fill='both', expand=True)
        vsb.pack(side='right', fill='y')
        
        for col in columns:
            veh_tree.heading(col, text=col.upper())
            veh_tree.column(col, width=100)
        
        # Cargar vehículos disponibles (no asignados a expedientes)
        df = self.app.db_manager.get_all_vehicles_df()
        
        def filter_vehicles(*args):
            veh_tree.delete(*veh_tree.get_children())
            search_term = search_var.get().lower().strip()
            
            for _, row in df.iterrows():
                # Verificar si ya está en un expediente
                if self.app.db_manager.is_vehiculo_in_expediente(row['interno']):
                    continue
                
                row_str = ' '.join(row.fillna('').astype(str).str.lower())
                if not search_term or search_term in row_str:
                    estado = "✅" if all([row['foto_path'], row['motor_path'], 
                                         row['chasis_path'], row['docu_path']]) else "⚠️"
                    veh_tree.insert('', 'end', values=(
                        row['interno'], row['dominio'], row['marca'], 
                        row['modelo'], estado
                    ))
        
        search_var.trace_add('write', filter_vehicles)
        filter_vehicles()
        
        btn_frame = ttk.Frame(content_frame)
        btn_frame.pack(pady=10)
        
        def agregar():
            sel = veh_tree.selection()
            if not sel:
                messagebox.showwarning("Atención", "Seleccioná un vehículo")
                return
            
            values = veh_tree.item(sel[0], 'values')
            interno = int(values[0])
            
            if self.app.db_manager.add_vehiculo_to_expediente(self.current_expediente_id, interno):
                messagebox.showinfo("Éxito", "Vehículo agregado al expediente")
                self._cargar_vehiculos_expediente()
                dialog.destroy()
            else:
                messagebox.showerror("Error", "No se pudo agregar el vehículo")
        
        ttk.Button(btn_frame, text="Cancelar", command=dialog.destroy).pack(side='right', padx=5)
        ttk.Button(btn_frame, text="Agregar", command=agregar).pack(side='right', padx=5)
        
    def _quitar_vehiculo(self):
        """Quita vehículo del expediente"""
        if not self.current_expediente_id:
            return
        
        sel = self.vehiculos_tree.selection()
        if not sel:
            messagebox.showwarning("Atención", "Seleccioná un vehículo")
            return
        
        values = self.vehiculos_tree.item(sel[0], 'values')
        interno = int(values[0])
        
        confirmar = messagebox.askyesno("Confirmar", 
            f"¿Quitar vehículo interno {interno} del expediente?")
        
        if confirmar:
            self.app.db_manager.remove_vehiculo_from_expediente(self.current_expediente_id, interno)
            self._cargar_vehiculos_expediente()
            self._refresh_expedientes()
            
    def _cerrar_expediente(self):
        """Cierra expediente"""
        if not self.current_expediente_id:
            return
        
        confirmar = messagebox.askyesno("Confirmar", "¿Cerrar este expediente?")
        if confirmar:
            self.app.db_manager.update_expediente_estado(self.current_expediente_id, 'CERRADO')
            self._refresh_expedientes()
            messagebox.showinfo("Éxito", "Expediente cerrado")
            
    def _eliminar_expediente(self):
        """Elimina expediente"""
        if not self.current_expediente_id:
            return
        
        confirmar = messagebox.askyesno("Confirmar", 
            "¿Eliminar este expediente permanentemente?")
        if confirmar:
            # Implementar eliminación en DB
            messagebox.showinfo("Info", "Función en desarrollo")
            
    def _generar_informe(self):
        """Genera informe del expediente"""
        if not self.current_expediente_id:
            return
        
        messagebox.showinfo("Info", "Generación de informe en desarrollo")


class VerificacionesView(ttk.Frame):
    """Vista de verificaciones"""
    
    def __init__(self, parent, app):
        super().__init__(parent, style='TFrame')
        self.app = app
        
        header_frame = ttk.Frame(self, style='TFrame')
        header_frame.pack(fill='x', padx=30, pady=20)
        
        ttk.Label(header_frame, text="✅ Verificaciones", 
                 style='Header.TLabel').pack()
        
        info_lbl = ttk.Label(self, text=" Módulo en desarrollo",
                            font=('Segoe UI', 14))
        info_lbl.pack(pady=50)


class InformesView(ttk.Frame):
    """Vista de informes"""
    
    def __init__(self, parent, app):
        super().__init__(parent, style='TFrame')
        self.app = app
        
        header_frame = ttk.Frame(self, style='TFrame')
        header_frame.pack(fill='x', padx=30, pady=20)
        
        ttk.Label(header_frame, text="📄 Informes", 
                 style='Header.TLabel').pack()
        
        # Botones de generación
        btn_frame = ttk.Frame(self, style='Card.TFrame', padding=30)
        btn_frame.pack(padx=30, pady=20)
        
        informes = [
            ("📊 IFGRA Completo", app.generar_ifgra_todos),
            ("🧪 IFGRA Prueba", app.generar_ifgra_prueba),
            ("📋 Inventario", app.generar_inventario_sel),
            ("📝 Informe Técnico", app.generar_informe_tecnico_sel),
            ("📥 Exportar Listado", app.generar_listado)
        ]
        
        for i, (text, command) in enumerate(informes):
            btn = ttk.Button(btn_frame, text=text, command=command, width=25)
            btn.grid(row=i//2, column=i%2, padx=10, pady=10)


class ConfiguracionView(ttk.Frame):
    """Vista de configuración"""
    
    def __init__(self, parent, app):
        super().__init__(parent, style='TFrame')
        self.app = app
        
        header_frame = ttk.Frame(self, style='TFrame')
        header_frame.pack(fill='x', padx=30, pady=20)
        
        ttk.Label(header_frame, text="⚙️ Configuración", 
                 style='Header.TLabel').pack()
        
        # Panel de configuración
        config_frame = ttk.LabelFrame(self, text="Opciones", padding=20)
        config_frame.pack(fill='both', expand=True, padx=30, pady=20)
        
        ttk.Label(config_frame, text="🚧 Configuración en desarrollo",
                 font=('Segoe UI', 14)).pack(pady=50)


# =============================================================================
# VENTANA PRINCIPAL
# =============================================================================
class MainWindow:
    """Ventana principal de la aplicación"""
    
    def __init__(self):
        # Crear ventana principal
        if USE_TTKBOOTSTRAP:
            self.root = ttkb.Window(themename='darkly')
        else:
            self.root = tk.Tk()
            ModernStyle.apply_dark_theme(self.root)
        
        self.root.title("🚗 Gestor de Flota Automotor 2026")
        self.root.geometry("1400x900")
        self.root.minsize(1200, 700)
        
        # Referencia al db_manager
        self.db_manager = db_manager
        
        # Inicializar DB
        self.db_manager.init_database()
        PathManager.ensure_directories()
        
        # Cargar aclaraciones guardadas
        self._load_aclaraciones()
        
        # Crear UI
        self._create_ui()
        
        # Bind para cerrar
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        
        logger.info("Aplicación iniciada correctamente")
        
    def _create_ui(self):
        """Crea la interfaz principal"""
        # Contenedor principal
        main_container = ttk.Frame(self.root)
        main_container.pack(fill='both', expand=True)
        
        # Sidebar
        self.sidebar = Sidebar(main_container, on_navigation=self._navigate)
        self.sidebar.pack(side='left', fill='y')
        
        # Área principal
        main_area = ttk.Frame(main_container, style='TFrame')
        main_area.pack(side='left', fill='both', expand=True)
        
        # Header
        header_frame = ttk.Frame(main_area, style='Card.TFrame')
        header_frame.pack(fill='x', padx=20, pady=10)
        
        self.header_title = ttk.Label(header_frame, text="📊 Dashboard", 
                                     style='Header.TLabel')
        self.header_title.pack(side='left', padx=20, pady=15)
        
        # Botones de acción rápida
        actions_frame = ttk.Frame(header_frame, style='Card.TFrame')
        actions_frame.pack(side='right', padx=20, pady=10)
        
        ttk.Button(actions_frame, text="🔄 Actualizar",
                  command=self._refresh_current_view).pack(side='left', padx=5)
        
        # Área de vistas
        self.views_container = ttk.Frame(main_area, style='TFrame')
        self.views_container.pack(fill='both', expand=True, padx=20, pady=10)
        
        # Crear vistas
        self.views = {}
        self.current_view = None
        
        self._create_views()
        
        # Mostrar dashboard por defecto
        self._navigate('dashboard')
        
    def _create_views(self):
        """Crea todas las vistas"""
        self.views['dashboard'] = DashboardView(self.views_container, self)
        self.views['vehiculos'] = VehiculosView(self.views_container, self)
        self.views['expedientes'] = ExpedientesView(self.views_container, self)
        self.views['verificaciones'] = VerificacionesView(self.views_container, self)
        self.views['informes'] = InformesView(self.views_container, self)
        self.views['configuracion'] = ConfiguracionView(self.views_container, self)
        
        # Ocultar todas inicialmente
        for view in self.views.values():
            view.pack_forget()
            
    def _navigate(self, view_id):
        """Navega a una vista"""
        if self.current_view:
            self.current_view.pack_forget()
        
        self.current_view = self.views.get(view_id)
        if self.current_view:
            self.current_view.pack(fill='both', expand=True)
        
        # Actualizar sidebar
        self.sidebar.set_active_view(view_id)
        
        # Actualizar header
        titles = {
            'dashboard': '📊 Dashboard',
            'vehiculos': '🚙 Vehículos',
            'expedientes': '📁 Expedientes',
            'verificaciones': '✅ Verificaciones',
            'informes': '📄 Informes',
            'configuracion': '⚙️ Configuración'
        }
        self.header_title.configure(text=titles.get(view_id, ''))
        
    def _refresh_current_view(self):
        """Refresca la vista actual"""
        if hasattr(self.current_view, '_refresh_tree'):
            self.current_view._refresh_tree()
        elif hasattr(self.current_view, '_refresh_expedientes'):
            self.current_view._refresh_expedientes()
            
    def _load_aclaraciones(self):
        """Carga aclaraciones guardadas"""
        aclaraciones_file = config.get_path('base_dir') / 'aclaraciones_guardadas.json'
        if aclaraciones_file.exists():
            try:
                with open(aclaraciones_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    # Convertir claves a int
                    if hasattr(self, 'current_view') and hasattr(self.current_view, 'aclaraciones_por_interno'):
                        self.current_view.aclaraciones_por_interno = {
                            int(k): v for k, v in data.items()
                        }
                logger.info("Aclaraciones cargadas")
            except Exception as e:
                logger.error(f"Error cargando aclaraciones: {e}")
                
    def _on_close(self):
        """Maneja cierre de aplicación"""
        # Guardar aclaraciones si existen
        if hasattr(self, 'current_view') and hasattr(self.current_view, '_save_aclaraciones'):
            self.current_view._save_aclaraciones()
        
        logger.info("Aplicación cerrada")
        self.root.destroy()
        
    # =============================================================================
    # MÉTODOS COMPATIBILIDAD (llamados desde las vistas)
    # =============================================================================
    def importar_excel(self):
        """Importa Excel (compatibilidad)"""
        path = filedialog.askopenfilename(
            title="Seleccionar archivo Excel",
            filetypes=[("Excel", "*.xlsx *.xls")]
        )
        if path:
            # Implementar lógica de importación
            messagebox.showinfo("Info", "Función de importación en desarrollo")
            
    def generar_ifgra_todos(self):
        """Genera IFGRA completo"""
        messagebox.showinfo("Info", "Generación IFGRA en desarrollo")
        
    def generar_ifgra_prueba(self):
        """Genera IFGRA de prueba"""
        messagebox.showinfo("Info", "Generación IFGRA prueba en desarrollo")
        
    def generar_inventario_sel(self):
        """Genera inventario"""
        messagebox.showinfo("Info", "Generación inventario en desarrollo")
        
    def generar_informe_tecnico_sel(self):
        """Genera informe técnico"""
        messagebox.showinfo("Info", "Generación informe técnico en desarrollo")
        
    def generar_listado(self):
        """Exporta listado"""
        messagebox.showinfo("Info", "Exportación en desarrollo")
        
    def run(self):
        """Inicia la aplicación"""
        self.root.mainloop()


# =============================================================================
# PUNTO DE ENTRADA
# =============================================================================
if __name__ == "__main__":
    try:
        app = MainWindow()
        app.run()
    except Exception as e:
        logger.critical(f"Error crítico: {str(e)}")
        raise
