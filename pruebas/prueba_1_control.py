"""
Prueba 1: control.

Portal sin fallos. El scraper recorre los 300 envíos de una vez.
Resultado esperado: termina con código 0 y los 300 coinciden con
esperado.csv (0 distintos, 0 pendientes).
"""
import sys

from _comun import informar
from verificar import Portal, comparar, correr_scraper, imprimir_reporte, preparar_carpeta, ultima_salida

NOMBRE = "Prueba 1: control"
ESPERADO = "código 0; 300 de 300 coinciden, 0 distintos, 0 pendientes"


def ejecutar():
    carpeta, entrada = preparar_carpeta("prueba_1_control")
    with Portal() as portal:
        codigo, _ = correr_scraper(entrada, portal, carpeta)

    salida = ultima_salida(carpeta)
    if salida is None:
        return False, f"código {codigo}; no se generó ningún archivo de salida"
    r = comparar(salida)
    imprimir_reporte(r)

    obtenido = (f"código {codigo}; {r['correctos']} de {r['total']} coinciden, "
                f"{len(r['distintos'])} distintos, {len(r['pendientes'])} pendientes")
    pasa = (codigo == 0 and r["total"] == 300 and r["correctos"] == 300
            and not r["distintos"] and not r["pendientes"])
    return pasa, obtenido


if __name__ == "__main__":
    sys.exit(informar(NOMBRE, ESPERADO, *ejecutar()))
