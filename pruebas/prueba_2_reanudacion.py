"""
Prueba 2: reanudación.

Portal sin fallos. El scraper se corta de golpe al empezar el pedido 131
(como si se cerrara la ventana: sin guardado de emergencia). Queda el
checkpoint lote_EN_CURSO.xlsx, que se guardó al llegar a 100. Se vuelve a
lanzar sobre ese checkpoint.

Resultado esperado:
  - el checkpoint tiene 100 envíos resueltos;
  - la segunda corrida termina (código 0), consulta los 200 que faltaban y no
    vuelve a consultar NINGUNO de esos 100 (se comprueba con el registro de
    consultas del portal);
  - la salida final tiene 300 filas, sin IDs duplicados, y las 300 coinciden.
Los pedidos 101 a 130 sí se consultan otra vez: se procesaron después del
último checkpoint y su resultado se perdió con el corte. Es lo esperado.
"""
import sys

import pandas as pd

from _comun import informar
from verificar import Portal, comparar, correr_scraper, imprimir_reporte, preparar_carpeta, ultima_salida

NOMBRE = "Prueba 2: reanudación"
ESPERADO = ("checkpoint con 100 resueltos; 2.ª corrida código 0, 200 consultas sin repetir ninguno de esos 100; "
            "300 filas, 0 IDs duplicados, 300 de 300 coinciden")
CORTAR_TRAS = 130


def ejecutar():
    carpeta, entrada = preparar_carpeta("prueba_2_reanudacion")
    registro = carpeta / "consultas.log"

    with Portal(registro_consultas=registro) as portal:
        codigo_1, _ = correr_scraper(entrada, portal, carpeta, matar_tras=CORTAR_TRAS)

        checkpoint = carpeta / "lote_EN_CURSO.xlsx"
        if not checkpoint.exists():
            return False, f"tras cortar en {CORTAR_TRAS} no existe {checkpoint.name}"
        cp = pd.read_excel(checkpoint, dtype=str)
        estados = cp["Estado_Scraping"].fillna("").str.strip()
        resueltos = set(cp.loc[estados != "", "ID_Envio"])
        consultas_antes = len(registro.read_text(encoding="utf-8").splitlines())

        codigo_2, _ = correr_scraper(checkpoint, portal, carpeta)
        consultas_2 = registro.read_text(encoding="utf-8").splitlines()[consultas_antes:]

    repetidos = resueltos & set(consultas_2)
    salida = ultima_salida(carpeta)
    r = comparar(salida)
    imprimir_reporte(r)

    obtenido = (f"1.ª corrida cortada tras {CORTAR_TRAS} pedidos; checkpoint con {len(resueltos)} resueltos; "
                f"2.ª corrida código {codigo_2}, {len(consultas_2)} consultas, "
                f"{len(repetidos)} de los resueltos repetidos; "
                f"{r['filas_salida']} filas, {r['ids_duplicados']} IDs duplicados, "
                f"{r['correctos']} de {r['total']} coinciden")
    # Contar las consultas evita que un registro vacío o perdido pase como «ninguno repetido».
    pasa = (codigo_1 is None and len(resueltos) == 100 and codigo_2 == 0
            and len(consultas_2) == 300 - len(resueltos) and not repetidos
            and r["filas_salida"] == 300 and r["ids_duplicados"] == 0 and r["correctos"] == 300)
    return pasa, obtenido


if __name__ == "__main__":
    sys.exit(informar(NOMBRE, ESPERADO, *ejecutar()))
