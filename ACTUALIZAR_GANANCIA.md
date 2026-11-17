# Dashboard: venta, comisión, despacho, costo y ganancia

## Qué cambia

El resumen principal ahora tiene cinco tarjetas:

1. **Venta total**: suma de órdenes pagadas del período seleccionado.
2. **Comisión Mercado Libre**: cargos por venta informados por la API.
3. **Cargo por despacho**: tu cargo de Mercado Envíos más el reparto Flex registrado.
4. **Costo de mercadería**: el costo unitario que ingresaste por las unidades vendidas.
5. **Ganancia de las ventas**: venta total menos comisión ML, despacho y mercadería.

Los gastos generales y bonificaciones Flex siguen disponibles en **Otros
movimientos del negocio**, separados de esa fórmula. El resultado después de
esos movimientos también se conserva. La ganancia principal es antes de impuestos,
gastos generales, bonificaciones y ajustes no importados.

Un cargo o costo desconocido aparece **Pendiente**, nunca como costo cero. Una
comisión o despacho informado explícitamente en cero sí se considera conocido.
El CSV y el listado por orden incluyen despacho, costo y ganancia de la venta.

## Corrección del despacho

La API de envíos puede devolver el vendedor en `origin.sender_id` en lugar de
`sender_id`. La versión anterior rechazaba esa estructura. Ahora admite ambas,
verifica que correspondan a tu vendedor y consulta su costo final en
`/shipments/{id}/costs`, sin atribuirte el costo pagado por el comprador.
No se infiere tu cargo desde la tarifa bruta ni desde porcentajes de descuento.

La corrección se probó con respuestas simuladas de ambos formatos; todavía debes
confirmarla con tu cuenta en Render. Si la API rechaza permisos o no informa el
cargo, verás el error en el dashboard y en el detalle de la venta.

## Cómo actualizar el proyecto que ya tienes

1. Descarga un respaldo desde el administrador del catálogo.
2. Descarga y descomprime el ZIP de esta versión.
3. Abre PowerShell dentro de la carpeta descomprimida **catalogo_productos**, donde
   están app.py y reportes.py. Desde ahí ejecuta este bloque:

```powershell
$origen = (Get-Location).Path
$destino = "C:\Users\comun\OneDrive\Escritorio\python\catalogo_productos"
if (!(Test-Path -LiteralPath (Join-Path $origen "desglose_envios.py"))) { throw "Abre la carpeta descomprimida catalogo_productos antes de continuar." }
if (!(Test-Path -LiteralPath (Join-Path $destino ".git"))) { throw "La carpeta de destino no es el repositorio original." }
if ($origen -eq $destino) { throw "Ejecuta este bloque desde la carpeta del ZIP, no desde el repositorio original." }
@("app.py", "mercadolibre.py", "categorias_ml.py", "reportes.py", "desglose_envios.py", "respaldos.py", "imagenes.py", "configurar.py", "requirements.txt", "templates", "static", "tests", "ACTUALIZAR_GANANCIA.md") | ForEach-Object {
    Copy-Item -LiteralPath (Join-Path $origen $_) -Destination $destino -Recurse -Force -ErrorAction Stop
}
Set-Location -LiteralPath $destino
git status
```

El bloque solo copia los archivos de código indicados. Conserva .env, data,
.venv y .git. No cambies las claves ni desconectes tu cuenta de Mercado Libre.

4. `git status` debe indicar la rama **pruebas-dashboard** que venías utilizando.
   Si es así, ejecuta:

```powershell
git add app.py mercadolibre.py categorias_ml.py reportes.py desglose_envios.py respaldos.py imagenes.py configurar.py requirements.txt templates static tests ACTUALIZAR_GANANCIA.md
git commit -m "Mostrar ganancia descontando comision despacho y mercaderia"
git push origin pruebas-dashboard
```

5. Espera el despliegue del servicio existente de Render, configurado para esa rama.

## Activar los datos de las ventas ya importadas

1. En **Dashboard y estadísticas**, selecciona las fechas de tus ventas.
2. Pulsa **Actualizar cargos de despacho**. Se agenda una consulta por envío,
   sin descargar de nuevo los productos ni las fotos.
3. Espera que terminen las tareas y pulsa **Actualizar resultados**.
4. En **Costos de productos**, ingresa cuánto te costó cada unidad. Deja marcada
   la opción de aplicarlo a ventas importadas que todavía no tengan costo.
5. Vuelve al dashboard y revisa las cinco tarjetas y el detalle de una venta.

Si el envío es Flex, registra lo que realmente pagaste al repartidor en el
detalle del envío. Ese desembolso externo no puede deducirse de la tarifa del
comprador y no debe registrarse otra vez como gasto general.

El neto calculado no certifica el depósito o la fecha de liberación de Mercado
Pago. Los importes se guardan con centavos para conservar cargos decimales;
una diferencia con un total de Mercado Pago debe investigarse, no inventarse.

## Validación

53 pruebas Python aprobadas: lectura de ambos formatos de envío, rechazo de otro
vendedor, precisión decimal, fórmula de ganancia, separación de gastos y Flex,
costos históricos, envíos compartidos, acceso privado, reintento de consulta,
exportación CSV, respaldos y clasificación de productos.
