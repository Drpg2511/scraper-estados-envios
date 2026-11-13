# Scraper de estados de envío, con reanudación y portal de prueba

Una tienda online despacha miles de paquetes por mes con una empresa de
transporte. Para saber qué pasó con cada uno (entregado, devuelto,
reexpedido, sin registro), alguien tenía que entrar al portal de clientes del
transportista, pegar el número de envío, leer el estado, volver atrás y
repetir. Con lotes de varios miles de envíos, es un día entero de trabajo.

Este scraper hace ese recorrido solo: lee un Excel con los números de envío,
abre un navegador real con Playwright, inicia sesión, consulta cada envío y
escribe el estado en una columna nueva. Construí la versión real para una
empresa de ecommerce española. Lo que hay acá es una **reconstrucción que
apunta a un portal falso incluido en el repo**, con 300 envíos inventados,
porque un scraper público no debería apuntar al sitio real de nadie.

![El scraper inicia sesión en el portal de demostración, consulta 12 envíos y deja el estado de cada uno en el Excel](docs/demo.gif)

## Qué resuelve

- **Lotes largos sin supervisión.** Un envío que no responde queda pendiente y
  el recorrido sigue; el lote no se cae por un pedido.
- **Se puede cortar y reanudar.** Las filas que ya tienen estado se saltan:
  si el proceso se corta, se vuelve a lanzar sobre el archivo parcial y sigue
  donde quedó, sin volver a consultar lo ya resuelto.
- **Casos raros del portal**: envíos tan viejos que ya no tienen registro, y
  envíos que existen pero no tienen estado asignado.
- **Sabe cuándo parar.** Si el problema ya no es de un envío sino del portal
  (caído, sesión caducada) o pide un CAPTCHA, corta el lote, guarda lo hecho y
  lo deja anotado en un registro.

## Lo que hace difícil este problema

Consultar un envío es fácil. Lo difícil es que el scraper corre horas contra
un sitio que no controlás, y **cuando algo sale mal, puede no notarse**: el
Excel se ve completo, pero con estados equivocados o con miles de celdas
vacías.

Casos reales que hubo que resolver, y que el portal falso reproduce:

| Caso | Qué pasaba | Cómo se resolvió |
|---|---|---|
| Un envío que no responde | Cortaba todo el lote: un solo tropiezo de conexión a mitad de camino dejaba cientos de envíos sin intentar | Hasta 3 intentos; si no, queda pendiente y se sigue |
| Envíos sin registro | El portal no muestra estado sino un aviso, y el scraper esperaba un estado que nunca llegaba | Se detecta el aviso → `Sin registro` |
| Envíos sin estado | La ficha existe pero la casilla viene vacía; reintentar no sirve | `Sin estado`, sin reintentos |
| Estado que llega tarde | La casilla se pinta vacía y el portal la rellena un momento después | Se sondea hasta 1,5 s antes de leer |
| **Falso «Sin registro» en cascada** | El botón Buscar envía el formulario ~15 ms *después* del clic, y la página anterior sigue viva ~400 ms. Tras un «sin resultados», el envío siguiente se leía sobre la página vieja | Se espera explícitamente a la página nueva (ver abajo) |
| Sesión caducada o portal caído | Cada envío esperaba 80 s, quedaba vacío y el scraper seguía: horas sin avanzar y sin avisar | Tras 5 envíos seguidos sin respuesta se corta el lote, con aviso |
| CAPTCHA en el login | Si el portal pide el puzzle, nadie se entera | No se intenta resolver: se anota el aviso y se corta |
| Números de envío de 16 dígitos | Excel los convertía a notación científica | La columna se lee como texto |
| El proceso muere de golpe | Se cierra la ventana o se va la luz y se pierde todo | Checkpoint cada 50 envíos |

### El falso «Sin registro», en detalle

Es el caso más traicionero porque no da error: escribe un estado plausible y
equivocado. El portal falso copia el comportamiento del real (formulario
enviado 15 ms después del clic). Con la lógica de lectura original:

| Condiciones | Envíos existentes leídos como «Sin registro» |
|---|---|
| Sin `slow_mo`, portal rápido | 10 de 10 |
| Sin `slow_mo`, portal que tarda 0,4 s como el real | 10 de 10 |
| Con `slow_mo=50`, como estaba configurado el original | 0 de 10 |

El original funcionaba **de casualidad**: con la pausa de 50 ms entre
acciones (`slow_mo`), Playwright llegaba a ver la navegación antes de leer. Bastaba con quitarla para ganar velocidad y el error
aparecía sin avisar. Ahora cada búsqueda espera a la página nueva
(`page.expect_navigation()`) y las pruebas corren **sin** `slow_mo`, que es
el caso más exigente.

## Cómo funciona la reanudación

Hay tres capas, de más fina a más gruesa:

1. **Por envío.** Si no responde tras 3 intentos, la celda queda vacía y se
   sigue con el siguiente.
2. **Por lote.** Si fallan 5 envíos seguidos, o pasa cualquier error
   inesperado, se escribe `<lote>_PARCIAL_<fecha>.xlsx` con todo lo capturado
   y se anota el corte en `avisos.log`.
3. **Por proceso.** Si el proceso muere sin pasar por el guardado de
   emergencia, queda `<lote>_EN_CURSO.xlsx`, que se reescribe cada 50 envíos.

Para reanudar no hay nada especial: se vuelve a lanzar eligiendo el PARCIAL o
el EN_CURSO. La condición que lo hace posible está al principio del bucle:
toda fila con texto en `Estado_Scraping` se salta. Al terminar, el scraper
dice cuántos envíos quedaron pendientes y qué archivo hay que relanzar.

Todos los archivos de salida se escriben junto al Excel de entrada y llevan
su nombre, así que dos lotes distintos no se pisan.

## Cómo se verifica

`portal_demo/datos.py` genera los 300 envíos a partir de una semilla fija y
escribe `esperado.csv` con el estado que el scraper debería obtener para cada
uno. `verificar.py` levanta el portal, corre el scraper y compara envío por
envío. Cada envío puede quedar **correcto**, **distinto** (estado
equivocado: lo grave) o **pendiente** (vacío: se completa relanzando).

Las tres pruebas de `pruebas/`:

| Prueba | Portal | Resultado esperado |
|---|---|---|
| 1. Control | Sin fallos | 300 de 300 correctos en una corrida |
| 2. Reanudación | Sin fallos; el scraper se mata de golpe en el envío 131 | El checkpoint tiene 100 resueltos; al relanzar se hacen exactamente las 200 consultas que faltaban, sin repetir ninguno de esos 100; la salida tiene 300 filas, sin duplicados, todas correctas |
| 3. Errores del portal | Error 500 cada 23 búsquedas, 3 % de respuestas de 12 s, sesión que caduca a las 110 búsquedas | Se corta con aviso; hay reintentos; **ningún estado equivocado** en ninguna corrida; relanzando, 300 de 300 |

```
python pruebas/correr_todas.py

Prueba 1: control
  Obtenido: código 0; 300 de 300 coinciden, 0 distintos, 0 pendientes
Prueba 2: reanudación
  Obtenido: 1.ª corrida cortada tras 130 pedidos; checkpoint con 100 resueltos;
            2.ª corrida código 0, 200 consultas, 0 de los resueltos repetidos;
            300 filas, 0 IDs duplicados, 300 de 300 coinciden
Prueba 3: errores del portal
  Corrida 1: código 2, 104 correctos, 196 pendientes, 0 distintos
  Corrida 2: código 2, 203 correctos,  97 pendientes, 0 distintos
  Corrida 3: código 0, 300 correctos,   0 pendientes, 0 distintos
  Obtenido: 2 cortes en avisos.log; 57 reintentos; 0 estados distintos en total

RESUMEN
  PASA   Prueba 1: control  (2.7 min)
  PASA   Prueba 2: reanudación  (2.9 min)
  PASA   Prueba 3: errores del portal  (5.0 min)
```

Las pruebas usan la carpeta `trabajo/` y cada una empieza vaciando la suya,
así que no hay que correr dos copias a la vez en la misma máquina.

## Cómo correrlo

Probado con Python 3.13 y Playwright 1.55 en Windows.

```bash
pip install -r requirements.txt
playwright install chromium
python portal_demo/datos.py          # genera esperado.csv y entrada_demo.xlsx
```

Portal y scraper a mano (dos terminales):

```bash
python portal_demo/servidor.py --puerto 8765
```

```bash
cp .env.example .env
python scraper_demo.py --entrada entrada_demo.xlsx
```

Sin `--entrada` se abre un selector de archivo, como en la versión original.
Para ver el portal en el navegador: http://127.0.0.1:8765 (usuario `demo`,
contraseña `demo1234`).

Verificación y pruebas:

```bash
python verificar.py
python verificar.py --error-cada 23 --caducar-tras 110 --lento-prob 0.03 --corridas 5
python pruebas/correr_todas.py
```

El portal acepta `--lento-prob`, `--lento-seg`, `--error-cada`,
`--caducar-tras` y `--verificacion-manual` para fallar a propósito.

## Estructura

```
scraper_demo.py        el scraper
portal_demo/
  servidor.py          portal falso (solo biblioteca estándar)
  datos.py             los 300 envíos ficticios y esperado.csv
esperado.csv           estado correcto de cada envío
verificar.py           corre el scraper y compara con esperado.csv
pruebas/               control, reanudación y errores del portal
```

Toda la configuración (URL, reintentos, esperas, corte) está en un bloque al
principio de `scraper_demo.py` y se puede cambiar desde el `.env`. Al lado de
cada valor figura el que se usaba contra el portal real.

## Límites conocidos

- **No vuelve a iniciar sesión** si la sesión caduca: corta y hay que
  relanzar. Con los lotes reales alcanzaba; agregarlo es el siguiente paso
  natural.
- **No resuelve CAPTCHAs.** Si el portal pide verificación manual, el lote
  se corta antes de empezar.
- **Depende de los selectores del portal.** Un rediseño del sitio rompe el
  login o la navegación; están todos juntos al principio del archivo para que
  cambiarlos sea un solo lugar.
