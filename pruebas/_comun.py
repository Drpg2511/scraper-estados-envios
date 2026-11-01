import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))


def informar(nombre, esperado, pasa, obtenido):
    print(f"\n{nombre}")
    print(f"  Esperado: {esperado}")
    print(f"  Obtenido: {obtenido}")
    print(f"  {'PASA' if pasa else 'FALLA'}")
    return 0 if pasa else 1
