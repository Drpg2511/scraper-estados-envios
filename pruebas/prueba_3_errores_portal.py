"""
Prueba 3: errores del portal.

El portal responde error 500 cada 23 búsquedas, tarda 12 s (más que la
espera del scraper) en un 3 % de ellas, y la sesión caduca tras 110
búsquedas. El scraper no vuelve a iniciar sesión: tras 5 pedidos seguidos sin
respuesta corta el lote, deja el PARCIAL y lo anota en avisos.log. Se lo
relanza sobre el parcial, hasta 5 veces, como haría una persona.

Resultado esperado:
  - la 1.ª corrida se corta (código 2) y queda el aviso en avisos.log;
  - hay reintentos (500 y respuestas lentas);
  - en NINGUNA corrida hay un envío con estado distinto del esperado
    (puede quedar pendiente, no mal);
  - relanzando, se termina con los 300 correctos.
"""
import sys

from _comun import informar
from verificar import Portal, comparar, correr_scraper, imprimir_reporte, preparar_carpeta, ultima_salida

NOMBRE = "Prueba 3: errores del portal"
ESPERADO = ("1.ª corrida cortada (código 2) con aviso registrado; hay reintentos; "
            "0 estados distintos en todas las corridas; al final 300 de 300 coinciden")
FALLOS = ("--error-cada", 23, "--caducar-tras", 110, "--lento-prob", 0.03, "--lento-seg", 12)
MAX_CORRIDAS = 5


def ejecutar():
    carpeta, entrada = preparar_carpeta("prueba_3_errores_portal")
    corridas = []
    with Portal(*FALLOS) as portal:
        actual = entrada
        for _ in range(MAX_CORRIDAS):
            codigo, lineas = correr_scraper(actual, portal, carpeta)
            salida = ultima_salida(carpeta)
            if salida is None:
                return False, f"corrida {len(corridas) + 1}: código {codigo}, sin archivo de salida"
            r = comparar(salida)
            corridas.append((codigo, r, lineas))
            print(f"  Corrida {len(corridas)}: código {codigo}, {r['correctos']} correctos, "
                  f"{len(r['pendientes'])} pendientes, {len(r['distintos'])} distintos")
            if codigo == 0:
                break
            actual = salida

    imprimir_reporte(corridas[-1][1])
    avisos_log = carpeta / "avisos.log"
    avisos = avisos_log.read_text(encoding="utf-8") if avisos_log.exists() else ""
    cortes = avisos.count("Lote cortado")
    reintentos = sum("Reintentando" in linea for _, _, lineas in corridas for linea in lineas)
    distintos = sum(len(r["distintos"]) for _, r, _ in corridas)
    codigo_final, r_final, _ = corridas[-1]

    obtenido = (f"{len(corridas)} corridas, códigos {[c for c, _, _ in corridas]}; "
                f"{cortes} cortes en avisos.log; {reintentos} reintentos; "
                f"{distintos} estados distintos en total; al final {r_final['correctos']} de {r_final['total']} coinciden")
    pasa = (corridas[0][0] == 2 and cortes >= 1 and reintentos > 0 and distintos == 0
            and codigo_final == 0 and r_final["correctos"] == 300)
    return pasa, obtenido


if __name__ == "__main__":
    sys.exit(informar(NOMBRE, ESPERADO, *ejecutar()))
