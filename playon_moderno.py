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