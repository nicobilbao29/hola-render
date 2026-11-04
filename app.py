import os
import secrets
import sqlite3
from pathlib import Path

from flask import (
    Flask,
    abort,
    redirect,
    render_template_string,
    request,
    session,
    url_for,
)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)

# Ahora guarda localmente. Después podremos apuntar al disco de Render.
CARPETA_DATOS = Path(
    os.environ.get("DATA_DIR", Path(__file__).parent / "data")
)
CARPETA_DATOS.mkdir(parents=True, exist_ok=True)

BASE_DATOS = CARPETA_DATOS / "practica.db"


def conectar():
    conexion = sqlite3.connect(BASE_DATOS, timeout=20)
    conexion.row_factory = sqlite3.Row
    return conexion


# Crea la tabla si todavía no existe.
conexion = conectar()

try:
    conexion.execute("""
        CREATE TABLE IF NOT EXISTS personas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            fecha_registro TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conexion.commit()
finally:
    conexion.close()


PAGINA = """
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Mi primera base de datos</title>

    <style>
        * { box-sizing: border-box; }

        body {
            margin: 0;
            padding: 40px 20px;
            background: #101827;
            color: #f5f7fa;
            font-family: Arial, sans-serif;
        }

        main {
            max-width: 750px;
            margin: auto;
        }

        .etiqueta {
            color: #7de2bc;
            letter-spacing: 2px;
            font-size: 12px;
        }

        h1 {
            font-size: clamp(32px, 6vw, 48px);
        }

        .descripcion {
            color: #b9c7db;
            line-height: 1.6;
        }

        .panel {
            margin-top: 28px;
            padding: 24px;
            background: #192438;
            border: 1px solid #344258;
            border-radius: 16px;
        }

        label {
            display: block;
            margin-bottom: 10px;
        }

        input {
            width: 100%;
            padding: 14px;
            border: 1px solid #526078;
            border-radius: 8px;
            font: inherit;
        }

        button {
            margin-top: 16px;
            padding: 14px 22px;
            background: #7de2bc;
            color: #101827;
            border: none;
            border-radius: 8px;
            font: bold 16px Arial, sans-serif;
            cursor: pointer;
        }

        button:hover { background: #a1efd2; }

        .tabla {
            overflow-x: auto;
        }

        table {
            width: 100%;
            border-collapse: collapse;
            text-align: left;
        }

        th, td {
            padding: 14px 10px;
            border-bottom: 1px solid #344258;
        }

        th { color: #7de2bc; }

        .aviso {
            padding: 12px;
            background: #55352a;
            border-radius: 8px;
        }
    </style>
</head>

<body>
    <main>
        <p class="etiqueta">PYTHON + FLASK + SQLITE</p>
        <h1>Mi primera base de datos</h1>

        <p class="descripcion">
            Escribe un nombre, guárdalo y comprueba cómo aparece
            en la lista.
        </p>

        <section class="panel">
            <h2>Registrar persona</h2>

            {% if error %}
                <p class="aviso" role="alert">{{ error }}</p>
            {% endif %}

            <form method="post">
                <input
                    type="hidden"
                    name="csrf_token"
                    value="{{ csrf_token }}"
                >

                <label for="nombre">Nombre</label>
                <input
                    id="nombre"
                    name="nombre"
                    maxlength="100"
                    placeholder="Ejemplo: Nicolás"
                    value="{{ nombre }}"
                    required
                >

                <button type="submit">Guardar nombre</button>
            </form>
        </section>

        <section class="panel">
            <h2>Personas registradas: {{ personas|length }}</h2>

            {% if personas %}
                <div class="tabla">
                    <table>
                        <thead>
                            <tr>
                                <th>ID</th>
                                <th>Nombre</th>
                                <th>Fecha (UTC)</th>
                            </tr>
                        </thead>

                        <tbody>
                            {% for persona in personas %}
                                <tr>
                                    <td>{{ persona.id }}</td>
                                    <td>{{ persona.nombre }}</td>
                                    <td>{{ persona.fecha_registro }}</td>
                                </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
            {% else %}
                <p class="descripcion">
                    Todavía no hay registros. Agrega el primero.
                </p>
            {% endif %}
        </section>

        <p class="descripcion">
            Práctica pública: usa nombres ficticios.
            En Render gratuito los datos son temporales.
        </p>
    </main>
</body>
</html>
"""


@app.route("/", methods=["GET", "POST"])
def inicio():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(32)

    error = None
    nombre = ""

    if request.method == "POST":
        token = request.form.get("csrf_token", "")

        if not secrets.compare_digest(token, session["csrf_token"]):
            abort(400, "Recarga la página y vuelve a enviar el formulario.")

        nombre = request.form.get("nombre", "").strip()

        if not 1 <= len(nombre) <= 100:
            error = "Escribe un nombre de entre 1 y 100 caracteres."
        else:
            conexion = conectar()

            try:
                conexion.execute(
                    "INSERT INTO personas (nombre) VALUES (?)",
                    (nombre,),
                )
                conexion.commit()
            finally:
                conexion.close()

            # Redirige para evitar duplicados al actualizar la página.
            return redirect(url_for("inicio"))

    conexion = conectar()

    try:
        personas = conexion.execute(
            "SELECT * FROM personas ORDER BY id DESC"
        ).fetchall()
    finally:
        conexion.close()

    return render_template_string(
        PAGINA,
        personas=personas,
        error=error,
        nombre=nombre,
        csrf_token=session["csrf_token"],
    )


if __name__ == "__main__":
    app.run()