# Tu proyecto actual, convertido en catálogo

Esta actualización parte del repositorio `nicobilbao29/hola-render`. Mantiene Flask y SQLite. Las nuevas tablas se crean dentro de `data/practica.db` (o en DATA_DIR). No borra la tabla `personas` de la práctica anterior. No incluye ninguna base de datos, credencial ni producto ficticio.

## 1. Actualizar en tu computador

1. Detén Flask con Ctrl+C.
2. Extrae este ZIP en otra carpeta.
3. Copia los archivos de la carpeta `catalogo_productos` al proyecto actual `hola_render`, reemplazando cuando corresponda. Copia también las carpetas `templates` y `static` completas. Conserva `.git`, `.venv`, `.env` y `data` de tu proyecto original. No ejecutes git init otra vez.
4. Desde VS Code, en la terminal del proyecto actual y con el entorno activo:

```powershell
python -m pip install -r requirements.txt
python configurar.py
python app.py
```

El configurador pide una contraseña de al menos 12 caracteres y la confirmación. No se muestran caracteres al escribir. Guarda su hash en `.env`, junto con SECRET_KEY. Si ya tienes una contraseña configurada y deseas mantenerla, omite `python configurar.py`. Ejecutarlo nuevamente cambia la contraseña. Nunca subas `.env` a GitHub.

5. Abre http://127.0.0.1:5000/catalogo
6. Entra a Administrar con tu contraseña.
7. En “Mi negocio y WhatsApp”, guarda el nombre que quieras mostrar, el texto de portada y tu número real. Usa el formato 56912345678, sin + ni espacios. El número se hará público al activar las consultas.

## 2. Agregar productos y categorías

- Categorías: crea y renombra las categorías desde el panel. No necesitas modificar Python.
- Productos: agrega nombre, categoría, descripción, precio CLP sin puntos y stock. Cero stock muestra Agotado. La consulta de WhatsApp no descuenta ni reserva stock.
- Fotos: selecciona varias manteniendo Ctrl o Shift. Máximo 8 por producto; JPG, PNG o WebP, hasta 6 MB cada una y 20 megapíxeles. Se optimizan hasta 1400 px y se guardan como WebP. Si el formulario tiene un error, por seguridad el navegador obliga a volver a seleccionar los archivos.
- Fotos de Mercado Libre: puedes pegar enlaces directos `https://http2.mlstatic.com/D_...`, uno por línea, además de subir archivos. Se guarda una copia, no se depende permanentemente del enlace remoto. Si Mercado Libre bloquea la descarga, usa archivos desde tu computador.
- Enlace de Mercado Libre: pega el enlace completo de tu publicación. Esto crea el botón “Ver en Mercado Libre”; no importa automáticamente el título, precio o fotos de la publicación.
- Portada: al editar un producto, los botones debajo de las fotos permiten quitar una imagen o elegir la portada. Guarda primero los demás cambios del formulario antes de usar esos botones.
- Destacado: los productos destacados aparecen primero en el orden predeterminado.
- Publicado: desmárcalo para ocultar un producto sin borrar sus datos.
- Galería: el visitante abre el producto y selecciona las miniaturas para ver las fotos.
- WhatsApp: el botón prepara una consulta con nombre, código, precio y enlace del producto. El visitante decide si envía el mensaje. Debes configurar tu número real para que el botón aparezca.

Las imágenes decorativas de la portada son imágenes que ya habías preparado para tus productos. No son productos cargados ni afirman precios o disponibilidad. Puedes sustituir `static/img/chal.webp` y `static/img/blazer.webp` para cambiar esa portada. `poncho.webp` queda como recurso disponible para tu diseño; no se agrega automáticamente a la base de datos.

## 3. Actualizar el mismo servicio de Render

Antes de desplegar, abre el servicio actual → Environment y añade estas variables:

| Key | Value |
|---|---|
| SECRET_KEY | Valor completo de SECRET_KEY desde el archivo local `.env`, sin las comillas exteriores |
| ADMIN_PASSWORD_HASH | Valor completo de ADMIN_PASSWORD_HASH desde `.env`, sin las comillas exteriores; no es la contraseña escrita directamente |
| TRUST_RENDER_PROXY | `1` |

No uses Generate para el hash. Puedes generar SECRET_KEY en Render si prefieres una clave distinta a la local. No compartas capturas con estos valores visibles.

Configura:

| Campo | Valor |
|---|---|
| Build Command | `pip install -r requirements.txt` |
| Start Command | `gunicorn --workers 1 --threads 4 app:app` |
| Health Check Path | `/health` |

Estas variables y comandos se configuran en el servicio existente, sin crear otro proyecto. En Render, las cookies de sesión son seguras automáticamente.

Después, desde la carpeta original del repositorio en tu computador:

```powershell
git status
git add app.py imagenes.py configurar.py requirements.txt .gitignore templates/ static/ LEEME_CATALOGO.md tests/
git commit -m "Convertir proyecto en catálogo de productos"
git push
```

Revisa que el listado a subir no incluya `.env`, `.venv` ni `data`. Si esos archivos ya estaban versionados antes, `.gitignore` por sí solo no deja de rastrearlos: revisa el estado antes de subir. No se publicaron cambios automáticamente desde el archivo que recibiste: se publicarán cuando hagas push y Render termine su despliegue.

## 4. URL con la palabra catálogo

La página principal se sirve en `/catalogo` y `/` redirige allí. En el servicio actual, el enlace será:

`https://hola-render-ln8g.onrender.com/catalogo`

Esto no cambia el subdominio del servicio. Para que también la parte anterior a `.onrender.com` diga `catalogo`, ese cambio se gestiona en Render, no en Python. Revisa Settings → Name en el servicio y verifica la URL que Render muestre tras guardar; cambiar el nombre de GitHub no cambia esa dirección. No se garantiza que `catalogo.onrender.com` esté disponible ni que renombrar el servicio modifique una URL ya asignada. Si Render conserva la dirección, un servicio nuevo con nombre `catalogo` (o `catalogo-nicolas`) permite solicitar una dirección basada en ese nombre, posiblemente con sufijo; eso sería un despliegue separado y exige trasladar los datos si los hay. También puedes conectar un dominio propio. No crees otro servicio ni borres el actual solo para cambiar el nombre antes de revisar las opciones del panel y respaldar.

## 5. Datos y fotos: local frente a Render

Tu computador y Render tienen bases de datos independientes. GitHub guarda el código, no los productos que agregas en el panel.

En Render gratuito puedes probar el catálogo, pero los datos y fotos cargados se pierden cuando el almacenamiento temporal se renueva. No consideres ese plan como almacenamiento de tu inventario definitivo.

Para conservarlos al desplegar o reiniciar, usa un servicio pagado con un disco persistente en `/var/data` y añade `DATA_DIR=/var/data`. El programa usará `/var/data/practica.db` y `/var/data/uploads/` automáticamente. No cambies esta ruta después de cargar datos sin planificar el traslado. El disco es de una sola instancia; puede haber una interrupción breve en cada despliegue.

Antes de cambiar de ubicación o publicar datos importantes, descarga un respaldo desde el panel. El respaldo incluye `data/practica.db` y `data/uploads`. Guarda una copia fuera de Render. Para restaurar localmente, detén Flask, conserva una copia de la carpeta actual y coloca el contenido de `data/` del respaldo dentro de tu DATA_DIR. En producción, la restauración exige acceso al disco del servicio y debe hacerse con la aplicación detenida para evitar reemplazar escrituras en curso. No hay importación de respaldos desde el navegador en esta versión.

El despliegue inicial de esta actualización puede perder los registros de la práctica en Render Free por el funcionamiento del alojamiento, aunque este código no borra las tablas. Guarda previamente cualquier registro de la práctica que necesites conservar. En el computador, los registros existentes se mantienen en el mismo archivo.

## 6. Archivos principales

| Archivo | Responsabilidad |
|---|---|
| app.py | Rutas, autenticación, consultas y creación de tablas |
| imagenes.py | Descarga de fotos de ML y validación/optimización |
| configurar.py | Configuración de acceso local |
| templates/catalog.html | Portada, filtros y listado de productos |
| templates/product.html | Detalle y galería |
| templates/edit.html | Formulario de productos |
| static/style.css | Diseño adaptable a celular |
| static/app.js | Cambio de foto y confirmación al quitar imágenes |

## 7. Validación

Se incluyen pruebas del acceso privado, CSRF, conservación de la tabla anterior, categorías, varias fotos, portada, eliminación de fotos, filtros, productos ocultos, validación de enlaces, enlaces de WhatsApp y descarga de respaldo. Ejecuta `python -m unittest discover -s tests -v` si necesitas verificar cambios posteriores.

La portada y la estructura adaptable se implementaron con CSS. La vista local no fue accesible desde el navegador remoto usado para revisar esta entrega; comprueba el aspecto en tu navegador al ejecutar el proyecto. Las importaciones de imágenes remotas se prueban con respuestas simuladas; no se garantiza que Mercado Libre permita descargar cada enlace.
