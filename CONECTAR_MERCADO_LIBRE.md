# Conectar tu catálogo con Mercado Libre

Ya registraste la aplicación. Ahora falta publicar esta versión y configurar sus credenciales. La conexión real solo se puede comprobar después de autorizar tu cuenta; las pruebas incluidas usan respuestas simuladas de la API.

## 1. Respalda y actualiza tu carpeta actual

Desde el administrador de tu catálogo desplegado, descarga un respaldo ZIP antes de subir cambios. Render gratuito puede perder la base y las fotos al desplegar o reiniciar.

Detén Flask con Ctrl+C. Extrae este ZIP y copia su contenido sobre los archivos de tu carpeta de proyecto actual. Conserva `.env`, `.venv`, `.git` y `data`. No borres ni recrees la base y no ejecutes configurar.py nuevamente si el acceso ya funciona.

Los archivos nuevos principales son `mercadolibre.py`, `templates/mercadolibre.html` y esta guía. También se actualizaron app.py, respaldos.py y las plantillas. Copia todos los archivos del paquete para que sean compatibles entre sí. requirements.txt conserva las mismas dependencias.

## 2. Publica en GitHub

En la terminal de Visual Studio Code, dentro de la carpeta conectada a tu repositorio:

```powershell
git status
git add .
git commit -m "Conectar catalogo con Mercado Libre"
git push origin main
```

El .gitignore incluido excluye `.env`, `.venv`, `data`, ZIP y cachés. Revisa git status: esos archivos no deben aparecer como archivos para publicar. Si antes habías agregado datos al repositorio, .gitignore no retira archivos ya rastreados.

Espera a que Render complete el despliegue de esta versión.

## 3. Variables en Render

Abre tu servicio de Render → Environment (Variables de entorno) → Edit (Editar).

Agrega estas tres variables, sin borrar SECRET_KEY, ADMIN_PASSWORD_HASH u otras que ya utilizas:

| Clave | Valor |
|---|---|
| ML_CLIENT_ID | El App ID / Client ID de tu aplicación de Mercado Libre |
| ML_CLIENT_SECRET | El Client Secret / clave secreta de esa misma aplicación |
| ML_REDIRECT_URI | https://hola-render-ln8g.onrender.com/mercadolibre/callback |

Pega cada valor completo y sin comillas, espacios o saltos de línea. Guarda los cambios y aplica el despliegue. No envíes el Client Secret por chat ni lo escribas en GitHub.

Mantén SECRET_KEY con un valor fijo para que no se invalide la sesión durante la autorización.

Para este proyecto usa en Settings (Configuración) → Start Command (Comando de inicio):

```text
gunicorn --workers 1 --threads 4 --timeout 120 app:app
```

El consumidor de notificaciones funciona en segundo plano dentro del proceso. Usa una sola instancia para esta aplicación con SQLite. No uses múltiples instancias con carpetas de datos independientes.

Si tienes activados registros de acceso propios de Gunicorn, evita incluir los parámetros de las URLs en el formato: el regreso OAuth incluye un código temporal. La aplicación no registra claves ni respuestas de tokens.

## 4. Configuración en Mercado Libre

Verifica los datos que ya ingresaste:

| Campo | Valor |
|---|---|
| Redirect URI | https://hola-render-ln8g.onrender.com/mercadolibre/callback |
| Notificaciones callbacks URL | https://hola-render-ln8g.onrender.com/mercadolibre/notificaciones |
| Flujos | Authorization Code y Refresh Token |
| Requiere PKCE | Activado |
| Negocio | Mercado Libre |
| Usuarios | Lectura si está disponible; si la pantalla solo ofrece Lectura y escritura, esa opción |
| Publicación y sincronización | Lectura si está disponible; si la pantalla solo ofrece Lectura y escritura, esa opción |
| Tópico | items |
| Otros permisos | Sin acceso |

El código importa datos desde Mercado Libre. No crea, modifica ni elimina tus publicaciones allí. Las únicas solicitudes POST a su API son las de autorización y renovación de acceso.

Después de desplegar, abre:

https://hola-render-ln8g.onrender.com/mercadolibre/notificaciones

Debe mostrar JSON con estado «listo». Eso confirma que la ruta existe; no significa que la cuenta esté autorizada todavía. Si antes llegaron avisos a la ruta inexistente, revisa en Mercado Libre que el tópico items siga habilitado y guarda nuevamente la configuración de notificaciones.

## 5. Autoriza e importa

1. Abre https://hola-render-ln8g.onrender.com/admin y entra con tu contraseña habitual.
2. Pulsa **Mercado Libre** en el menú.
3. Pulsa **Conectar mi cuenta**.
4. Mercado Libre abrirá su pantalla de autorización. Autoriza con tu cuenta de vendedor de Chile.
5. Volverás al catálogo: debe mostrar **Cuenta conectada**.
6. Pulsa **Importar y actualizar productos**.
7. Pulsa **Actualizar estado** para ver el avance; puede tardar según el número de publicaciones.
8. Revisa un producto del catálogo y su botón de compra en Mercado Libre.

Las publicaciones se identifican por su código MLC: sincronizar dos veces actualiza el mismo registro. Los productos nuevos quedan en la categoría Mercado Libre. Puedes moverlos a otra categoría y marcarlos como destacados; esas dos elecciones se conservan.

Se actualizan nombre, descripción, precio CLP, disponibilidad, enlace, fotos y estado. Las publicaciones pausadas, cerradas o eliminadas se ocultan. Un error de red o permisos no borra el producto: queda pendiente para reintentar.

## 6. Tu uso diario

Publica o modifica tus productos en Mercado Libre. Sus avisos se guardan en una cola y luego se consulta la API para actualizar el catálogo. Las notificaciones duplicadas no generan productos duplicados. El acceso se renueva automáticamente mientras la autorización siga vigente.

Las fotos se muestran usando URLs HTTPS del CDN de Mercado Libre, sin descargarlas al disco. Si Mercado Libre retira una imagen o no está disponible, esa foto puede dejar de verse. La opción Fotos de portada permite seleccionar también fotos importadas: en ese caso sí se descarga una copia independiente para la portada.

Para productos exclusivos de tu sitio, continúa usando **Productos → Agregar producto**. Los productos manuales no son modificados por la integración. Si ya copiaste manualmente una publicación, su copia anterior no se enlaza automáticamente y puede aparecer junto a la nueva importada; revisa y oculta la copia manual.

Si falta un cambio o el servicio estuvo detenido, pulsa **Importar y actualizar productos**. Los reintentos no sustituyen esta revisión cuando el proveedor haya dejado de enviar avisos.

## 7. Persistencia y respaldos

El código usa la misma carpeta DATA_DIR del catálogo. Se agregan automáticamente tablas para relacionar publicaciones y fotos, sin borrar las existentes.

La autorización, los tokens renovables y la cola se guardan en `data/mercadolibre_privado.sqlite` (o dentro de DATA_DIR). Ese archivo es privado: no se publica en GitHub ni se incluye en Descargar respaldo. Las credenciales de la aplicación permanecen en las variables de Render.

El respaldo normal incluye productos, vínculos MLC y URLs de fotos. Las fotos remotas no se descargan dentro del ZIP. Al restaurar no se modifica la autorización actual. Si la autorización privada se perdió, vuelve a conectar la misma cuenta y sincroniza. Los respaldos de versiones anteriores a Mercado Libre se siguen admitiendo, pero no contienen esos vínculos.

En Render gratuito tanto el catálogo como la autorización y la cola pueden desaparecer al reiniciar o desplegar. Para conservarlos con esta arquitectura, el servicio de pago debe tener un disco persistente montado en `/var/data` y la variable `DATA_DIR=/var/data`. El simple cambio de variable no mueve los datos existentes: primero respalda y restaura, y vuelve a autorizar la cuenta si hace falta.

Render gratuito también se suspende por inactividad: puede retrasar o perder avisos. Este entorno sirve para probar; no garantiza sincronización continua. El servicio debe estar despierto y tener almacenamiento persistente para operar de forma sostenida.

## 8. Mensajes habituales

- **Faltan variables:** revisa ML_CLIENT_ID, ML_CLIENT_SECRET y ML_REDIRECT_URI en el servicio correcto de Render.
- **Redirect URI no válido:** las direcciones en Mercado Libre y Render deben coincidir exactamente, incluido HTTPS, ruta y ausencia de barra final.
- **Autorización vencida o sesión incorrecta:** entra otra vez a administración y pulsa Conectar mi cuenta. No abras el regreso OAuth manualmente.
- **Acceso rechazado (403):** revisa los permisos y vuelve a autorizar después de cambiarlos. Mercado Libre puede exigir verificaciones adicionales de la cuenta o aplicación.
- **Cuenta de otro vendedor:** usa la misma cuenta chilena ya vinculada. Usa Cambiar cuenta en el panel para desconectar la cuenta anterior antes de autorizar otro vendedor.
- **Pendientes con error:** lee el motivo en el panel y reintenta con Importar y actualizar productos.

## Verificación técnica incluida

```text
python -m unittest discover -s tests -v
node tests/carousel.test.cjs
```

Las pruebas cubren autorización PKCE/state, renovación de tokens, validación de avisos, cola, sincronización sin duplicados, productos manuales, fotos remotas, portadas y restauración. No requieren ni incluyen credenciales reales.

Documentación oficial consultada:
- https://developers.mercadolibre.cl/autenticacion-y-autorizacion
- https://developers.mercadolibre.cl/permisos-funcionales
- https://developers.mercadolibre.cl/es_ar/productos-recibe-notificaciones
- https://developers.mercadolibre.cl/items-y-busquedas


## 9. Si autorizaste una cuenta equivocada

App ID y Client Secret identifican la aplicación; el vendedor queda definido por la cuenta con la que se autoriza en Mercado Libre. Cambiar las claves en Render no cambia ni borra una autorización ya guardada.

1. Actualiza a esta versión conservando .env y data. Descarga un respaldo antes de desplegar en Render gratuito.
2. Abre Administración → Mercado Libre → Cambiar cuenta de Mercado Libre.
3. Lee y marca la confirmación. Pulsa Desconectar para cambiar cuenta.
4. Se ocultan los productos importados, conservando sus datos y vínculos. Los productos manuales y las fotos de portada no cambian; revisa las portadas si habías elegido imágenes de la cuenta anterior. Se cancelan tareas pendientes y se elimina la autorización local. No se borran publicaciones de Mercado Libre ni se revoca el permiso en el proveedor.
5. Abre una ventana privada o de incógnito. En esa ventana inicia sesión en Mercado Libre con tu vendedor correcto y verifica su usuario.
6. En la misma ventana, entra al administrador de tu catálogo y pulsa Conectar mi cuenta. Autoriza la integración.
7. Revisa el nombre de usuario y el número de vendedor que muestra el panel. Después pulsa Importar y actualizar productos.

Si hay una sincronización en curso, el panel te pedirá esperar antes de desconectar para evitar mezclar operaciones. Si se perdieron los tokens con el despliegue gratuito, puedes conectar directamente; las publicaciones que vuelvas a importar se identifican por su código MLC.

Opcional: si conoces el ID del vendedor correcto, agrega en Render `ML_EXPECTED_SELLER_ID` con ese número. La aplicación rechazará autorizar a otro vendedor, aunque el navegador tenga una sesión distinta. Ese número no es el App ID: corresponde al usuario vendedor. No adivines el valor; confírmalo en la cuenta correcta. Esta variable no cambia el vendedor ya guardado: usa primero Cambiar cuenta si hay una conexión equivocada.

Para subir este ajuste, copia del ZIP estos cuatro archivos sobre tu proyecto actual: app.py, mercadolibre.py, templates/mercadolibre.html y CONECTAR_MERCADO_LIBRE.md. Desde tu carpeta ya conectada a GitHub:

```powershell
git add app.py mercadolibre.py templates/mercadolibre.html CONECTAR_MERCADO_LIBRE.md
git commit -m "Permitir cambiar cuenta de Mercado Libre"
git push origin main
```

No necesitas crear otra aplicación de Mercado Libre para cambiar de vendedor, siempre que la aplicación permita autorizar esa cuenta. Si Mercado Libre rechaza el acceso, revisa la elegibilidad y los permisos de la cuenta y de la aplicación.
