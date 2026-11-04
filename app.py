from flask import Flask

app = Flask(__name__)


@app.route("/")
def inicio():
    return "<h1>Hola mundo</h1><p>Mi primera aplicación en Render.</p>"


if __name__ == "__main__":
    app.run()