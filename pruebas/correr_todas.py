"""Corre las tres pruebas y muestra un resumen. Tarda varios minutos."""
import sys
import time

from _comun import informar
import prueba_1_control
import prueba_2_reanudacion
import prueba_3_errores_portal

resultados = []
for prueba in (prueba_1_control, prueba_2_reanudacion, prueba_3_errores_portal):
    print(f"\n=== {prueba.NOMBRE} ===")
    inicio = time.time()
    try:
        pasa, obtenido = prueba.ejecutar()
    except Exception as e:  # una prueba que revienta cuenta como fallida
        pasa, obtenido = False, f"error al ejecutar la prueba: {e!r}"
    informar(prueba.NOMBRE, prueba.ESPERADO, pasa, obtenido)
    resultados.append((prueba.NOMBRE, pasa, time.time() - inicio))

print("\nRESUMEN")
for nombre, pasa, segundos in resultados:
    print(f"  {'PASA ' if pasa else 'FALLA'}  {nombre}  ({segundos / 60:.1f} min)")
sys.exit(0 if all(p for _, p, _ in resultados) else 1)
