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

Antes de cambiar de ubicación o publicar datos importantes, descarga un respaldo desde el panel. El respaldo incluye `data/practica.db` y `data/uploads`. Guarda una copia fuera de Render. Para restaurar localmente, detén Flask, conserva una copia de la carpeta actual y coloca el contenido de `data/` del respaldo dentro de tu DATA_DIR. En producción, la restauración exige acceso al disco del servicio y debe hacerse con la aplicación detenida para evitar reemplazar escrituras en curso. Esta versión incluye restauración del catálogo desde Administrar → Restaurar respaldo; consulta el apartado siguiente. Para una restauración manual del archivo completo conserva las precauciones de detener la aplicación.

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


## 8. Restaurar el catálogo desde la propia página

### Instalar esta actualización

Antes de desplegar cambios en Render Free, descarga un respaldo actual y guárdalo en tu computador. Publicar la actualización puede renovar el almacenamiento temporal.

Detén Flask y copia solamente estos cuatro archivos desde la versión nueva a tu proyecto actual:

- `app.py` (reemplazar).
- `respaldos.py` (nuevo; va junto a app.py).
- `templates/admin_base.html` (reemplazar).
- `templates/restore.html` (nuevo).

Conserva `.env`, `.venv`, `.git` y `data`. No vuelvas a ejecutar configurar.py: tu contraseña sigue funcionando. No hay nuevas dependencias. También puedes actualizar esta guía y las pruebas.

Para probar localmente, ejecuta `python app.py` o el comando Flask con puerto 5001 que estabas usando. Luego inicia sesión y entra a Restaurar respaldo.

Para publicar:

```powershell
git add app.py respaldos.py templates/admin_base.html templates/restore.html
git commit -m "Agregar restauración de respaldos del catálogo"
git push
```

En Render, para dar margen a las restauraciones, el comando de inicio puede ser:

```text
gunicorn --workers 1 --threads 4 --timeout 120 app:app
```

### Usar Restaurar respaldo

1. Entra al panel publicado con tu contraseña.
2. Pulsa Restaurar respaldo.
3. Selecciona el archivo ZIP original generado por Descargar respaldo. No lo descomprimas ni lo conviertas en RAR. Se admiten respaldos descargados antes de esta actualización.
4. Pulsa Revisar respaldo. Se validan las tablas, relaciones y fotos, y se muestran sus cantidades. Esta revisión aún no cambia el catálogo.
5. Marca que entiendes el reemplazo y pulsa Confirmar y restaurar. Puedes cancelar antes de confirmar.
6. Abre el catálogo y comprueba los productos y fotos recuperados.

Restaura productos, categorías, galería y configuración del negocio (incluido WhatsApp). Reemplaza esos datos; no mezcla productos nuevos con los anteriores. No cambia contraseñas, SECRET_KEY ni `.env`. No restaura tablas ajenas al catálogo, como `personas`: las que ya existan en destino se conservan. Aunque el ZIP incluye el archivo SQLite completo para análisis y respaldo, la importación web acepta únicamente las cuatro tablas conocidas del catálogo y no ejecuta SQL del ZIP.

Antes de reemplazar los datos, crea un ZIP automático del estado actual en `DATA_DIR/respaldos_automaticos`. Se puede descargar desde la misma pantalla para restaurarlo si quieres volver atrás. Las copias están en el mismo servicio; descárgalas fuera de Render para conservarlas. Se muestran las últimas diez y no se eliminan automáticamente. Su acumulación ocupa disco.

Límites: 100 MB por ZIP, 300 MB descomprimidos, base de hasta 32 MB, hasta 5.000 productos y 16.000 fotos en total, máximo 8 por producto. La revisión vence a los 30 minutos. Si una copia supera esos límites requiere un traslado manual. Las fotos se validan y optimizan nuevamente, por lo que sus nombres internos pueden cambiar sin alterar los productos. No reemplaces manualmente archivos mientras se esté restaurando.

Si hay errores de validación, falta una foto o falla la escritura antes de terminar, el catálogo anterior se conserva. Las operaciones de administración se coordinan con un bloqueo de archivos entre procesos, y el reemplazo de las tablas usa una transacción. Los lectores ven el estado anterior o el nuevo; una pestaña abierta con imágenes anteriores puede requerir recargar después de restaurar.

**Render gratuito:** la función funciona, pero los datos restaurados y las copias automáticas siguen siendo temporales. Restaurar un ZIP no añade persistencia al servicio. Para conservarlos al reiniciar o desplegar usa disco persistente y `DATA_DIR=/var/data`.

## 9. Categorías, carruseles y presentación de productos

Esta actualización no modifica el esquema de la base de datos. Los respaldos anteriores del catálogo siguen siendo compatibles.

- Puedes crear categorías desde Administrar → Categorías, desde el botón Nueva categoría del listado, o dentro del formulario de producto desplegando “Crear una categoría nueva”. Si ya existe, se utiliza la categoría existente. Se guarda junto con el producto, solo si el formulario es válido.
- La portada muestra tres imágenes en un carrusel que cambia cada 5 segundos. Puedes usar flechas, puntos y el botón de pausa. Sus archivos siguen en `static/img/chal.webp`, `blazer.webp` y `poncho.webp`.
- La galería del producto cambia cada 5,5 segundos cuando hay más de una foto. Las miniaturas y flechas permiten elegir una imagen; una selección manual pausa el cambio automático. Reproducir lo activa nuevamente. Con una sola foto se muestra una imagen fija.
- Los carruseles pausan con el foco, el puntero o al ocultar la pestaña. Si el navegador tiene la preferencia de movimiento reducido, comienzan pausados.
- Las fotos de las tarjetas y el detalle se muestran dentro de marcos uniformes de proporción 4:5, centradas y sin estirar ni recortar la prenda. Las tarjetas mantienen precios y botones alineados aunque los títulos tengan distinto largo. La portada recorta sus fotos de forma decorativa; no altera los archivos de los productos.
- Las tarjetas con un enlace de Mercado Libre ahora muestran “Comprar en Mercado Libre” directamente. Ese botón abre la publicación; no agrega un sistema de pagos propio. Si está agotado, dice “Ver en Mercado Libre”. El detalle conserva el contacto por WhatsApp.
- La sección Cómo comprar explica el proceso. No se añadieron reseñas, certificaciones, sellos ni promesas de entrega que no estén comprobados.

### Actualizar esta versión

Descarga un respaldo antes de publicar, especialmente si usas Render gratuito. Copia a tu proyecto los archivos `app.py`, `templates/catalog.html`, `templates/product.html`, `templates/edit.html`, `templates/admin.html`, `templates/base.html`, `static/app.js` y `static/style.css`. Conserva los demás archivos de restauración de la versión anterior (`respaldos.py`, `templates/restore.html`, `templates/admin_base.html`) o copia el proyecto completo del ZIP conservando tu `.env`, `.venv`, `.git` y `data`.

No necesitas volver a configurar la contraseña ni instalar nuevas dependencias.

```powershell
git add app.py templates/ static/
git commit -m "Mejorar categorías, carruseles y diseño del catálogo"
git push
```

Tras el despliegue, actualiza la página con Ctrl+F5 si tu navegador muestra el diseño anterior. Las pruebas funcionales verifican categorías, enlaces y HTML del carrusel; revisa también su aspecto y comportamiento desde el navegador y celular que utilices.

## 10. Elegir las fotos de la página principal

Entra a `/admin` con tu contraseña y abre **Fotos de portada**.

1. Sube una o varias imágenes desde tu computador, o elige una foto de un producto usando las miniaturas o el selector.
2. Escribe un texto opcional y pulsa **Agregar a la portada**.
3. Usa **Subir** y **Bajar** para cambiar el orden.
4. Marca o desmarca **Mostrar en la página principal** y pulsa **Guardar cambios**. También puedes editar el texto o quitar una foto.
5. Abre **Ver página principal** para comprobar el resultado.

Hasta 10 fotos de portada, JPG/PNG/WEBP de hasta 6 MB cada una. Las visibles cambian cada 5 segundos. Con una foto, la portada queda fija. Si no hay fotos visibles, vuelven las tres fotos de ejemplo. Una foto elegida de un producto se copia: quitarla del producto no rompe la portada.

Los respaldos ZIP incluyen las portadas, sus textos, orden y visibilidad. Los respaldos anteriores a esta función también se pueden restaurar; como no tienen portadas personalizadas, se mostrarán las fotos de ejemplo.

### Actualizar tu proyecto existente

Antes de actualizar Render gratuito, descarga un respaldo desde el panel: un despliegue puede borrar sus datos temporales.

Detén Flask con Ctrl+C. Copia los archivos del ZIP dentro de tu carpeta de proyecto actual, reemplazando los archivos de código. Conserva `.env`, `.venv`, `.git` y `data` de tu proyecto. No necesitas ejecutar configurar.py otra vez ni borrar la base de datos. Al iniciar, se crea automáticamente la nueva tabla catalogo_portadas sin borrar las tablas existentes.

Archivos de esta mejora: app.py, respaldos.py, templates/covers.html, templates/catalog.html, templates/admin_base.html, templates/restore.html, static/app.js y static/style.css.

Ejecuta `python app.py`. Entra a administración y revisa Fotos de portada. Para publicar, desde tu carpeta conectada a GitHub:

```powershell
git add app.py respaldos.py templates/ static/ LEEME_CATALOGO.md tests/
git commit -m "Agregar menu para administrar fotos de portada"
git push origin main
```

En Render gratuito, restaura el respaldo desde el panel si los datos se pierden al desplegar. Las fotos de portada también necesitan almacenamiento persistente para mantenerse entre reinicios sin restauración manual.

## 11. Conexión con Mercado Libre

Esta versión agrega el menú **Mercado Libre** con autorización OAuth PKCE, importación de publicaciones chilenas y actualización por avisos del tópico items. Las fotos de las publicaciones se muestran desde Mercado Libre; los productos manuales y fotos locales se mantienen separados. La configuración completa y los pasos para Render están en **CONECTAR_MERCADO_LIBRE.md**. Las pruebas de la integración utilizan una API simulada; falta autorizar tu cuenta para comprobar el acceso real.
