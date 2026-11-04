from flask import Flask

app = Flask(__name__)


@app.route("/")
def inicio():
    return """
    <!DOCTYPE html>
    <html lang="es">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Nicolás | Mi primera web</title>

        <style>
            * {
                box-sizing: border-box;
            }

            body {
                margin: 0;
                min-height: 100vh;
                background: #101827;
                color: #f5f7fa;
                font-family: Arial, sans-serif;
            }

            .contenedor {
                max-width: 1050px;
                margin: auto;
                padding: 30px 24px;
            }

            header {
                display: flex;
                justify-content: space-between;
                align-items: center;
                gap: 20px;
            }

            .logo {
                font-size: 22px;
                font-weight: bold;
            }

            .logo span {
                color: #7de2bc;
            }

            .etiqueta {
                border: 1px solid #344258;
                border-radius: 30px;
                padding: 9px 14px;
                color: #b9c7db;
                font-size: 12px;
            }

            main {
                padding: 90px 0 60px;
            }

            .titulo-pequeno {
                color: #7de2bc;
                font-size: 13px;
                letter-spacing: 3px;
            }

            h1 {
                max-width: 800px;
                margin: 24px 0;
                font-size: clamp(44px, 8vw, 82px);
                line-height: 1.08;
                letter-spacing: -3px;
            }

            h1 span {
                color: #7de2bc;
            }

            .descripcion {
                max-width: 580px;
                color: #b9c7db;
                font-size: 19px;
                line-height: 1.7;
            }

            .boton {
                display: inline-block;
                margin-top: 24px;
                padding: 16px 24px;
                background: #7de2bc;
                color: #101827;
                font-weight: bold;
                text-decoration: none;
                border-radius: 12px;
                transition: background 0.2s, transform 0.2s;
            }

            .boton:hover {
                background: #a1efd2;
                transform: translateY(-3px);
            }

            .boton:focus-visible {
                outline: 3px solid white;
                outline-offset: 5px;
            }

            .tarjetas {
                display: grid;
                grid-template-columns: repeat(3, 1fr);
                gap: 18px;
                margin-top: 65px;
            }

            .tarjeta {
                background: #192438;
                padding: 26px;
                border: 1px solid #2c3a50;
                border-radius: 18px;
            }

            .numero {
                color: #7de2bc;
                font-size: 13px;
            }

            .tarjeta h2 {
                font-size: 21px;
                margin: 16px 0 10px;
            }

            .tarjeta p {
                color: #b9c7db;
                line-height: 1.6;
                margin-bottom: 0;
            }

            footer {
                border-top: 1px solid #2c3a50;
                padding: 24px 0;
                color: #b9c7db;
                font-size: 13px;
            }

            @media (max-width: 650px) {
                main {
                    padding-top: 60px;
                }

                .tarjetas {
                    grid-template-columns: 1fr;
                    margin-top: 40px;
                }

                h1 {
                    letter-spacing: -2px;
                }
            }
        </style>
    </head>

    <body>
        <div class="contenedor">
            <header>
                <div class="logo">Nicolás<span>.</span></div>
                <span class="etiqueta">Mi primer proyecto web</span>
            </header>

            <main>
                <p class="titulo-pequeno">PYTHON · FLASK · DESARROLLO WEB</p>

                <h1>De una idea<br>a una <span>página real.</span></h1>

                <p class="descripcion">
                    Hola, soy Nicolás. Estoy aprendiendo a construir
                    aplicaciones con Python y a compartir mis
                    proyectos en internet.
                </p>

                <a
                    class="boton"
                    href="https://github.com/nicobilbao29/hola-render"
                    target="_blank"
                    rel="noopener noreferrer"
                >
                    Ver el código en GitHub ↗
                </a>

                <section class="tarjetas" aria-label="Cómo funciona este proyecto">
                    <article class="tarjeta">
                        <span class="numero">01 / CONSTRUIR</span>
                        <h2>Python y Flask</h2>
                        <p>El código recibe la visita y entrega esta página.</p>
                    </article>

                    <article class="tarjeta">
                        <span class="numero">02 / GUARDAR</span>
                        <h2>GitHub</h2>
                        <p>El repositorio guarda el código y su historial de cambios.</p>
                    </article>

                    <article class="tarjeta">
                        <span class="numero">03 / PUBLICAR</span>
                        <h2>Render</h2>
                        <p>El alojamiento ejecuta la aplicación en internet.</p>
                    </article>
                </section>
            </main>

            <footer>
                Creado por Nicolás Bilbao · Aprendiendo con proyectos reales.
            </footer>
        </div>
    </body>
    </html>
    """


if __name__ == "__main__":
    app.run()