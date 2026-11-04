from flask import Flask

app = Flask(__name__)


@app.route("/")
def inicio():
    return "<h1>Hola, soy Nicolás</h1><p>Actualicé mi primera página.</p>"


if __name__ == "__main__":
    app.run()