"""
Envíos ficticios del portal de demostración.

Todo sale de una semilla fija: el servidor, el Excel de entrada y
esperado.csv ven siempre los mismos 300 envíos. Así se puede comparar lo
que devuelve el scraper contra lo que el portal "sabe" de verdad.

Uso:
    python portal_demo/datos.py      # escribe esperado.csv y entrada_demo.xlsx
"""
import csv
import random
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

SEMILLA = 2026
TOTAL_ENVIOS = 300

# Credenciales del portal falso. Son de prueba y solo sirven contra él.
USUARIO_DEMO = "demo"
CONTRASENA_DEMO = "demo1234"

RAIZ = Path(__file__).resolve().parent.parent

# Estados que muestra la casilla "Estado" del portal, con su peso relativo.
ESTADOS = [
    ("ENTREGADO", 55),
    ("EN REPARTO", 8),
    ("EN TRÁNSITO", 8),
    ("DEVUELTO", 7),
    ("REEXPEDIDO", 5),
    ("DISPONIBLE EN PUNTO DE RECOGIDA", 4),
    ("INCIDENCIA EN ENTREGA", 3),
]
PROB_NO_ENCONTRADO = 0.06   # el portal no tiene registro del envío
PROB_SIN_ESTADO = 0.03      # el envío existe pero la casilla Estado viene vacía

# Posiciones forzadas para que siempre haya rachas de "no encontrados"
# seguidas de envíos que sí existen: es el caso que destapaba el falso
# "Sin registro" en cascada.
NO_ENCONTRADOS_FORZADOS = {10, 11, 12, 40, 41, 150}

ZONAS = ["Zona Norte", "Zona Sur", "Zona Centro", "Zona Este", "Zona Oeste", "Islas"]
INCIDENCIAS = {
    "DEVUELTO": ["Destinatario ausente en dos intentos", "Devuelto al remitente"],
    "INCIDENCIA EN ENTREGA": ["Dirección incompleta", "Destinatario ausente"],
    "REEXPEDIDO": ["Cambio de dirección solicitado por el destinatario"],
}


def generar_envios(semilla=SEMILLA, total=TOTAL_ENVIOS):
    """
    Devuelve la lista de envíos en el orden del Excel de entrada. Cada uno:
      id, en_portal, estado (texto de la casilla, "" si no tiene),
      admision, ultimo_movimiento, origen, destino, incidencias, asociado
    """
    rng = random.Random(semilla)
    nombres, pesos = zip(*ESTADOS)
    ids_usados = set()
    envios = []

    for posicion in range(total):
        while True:
            id_envio = f"7700{rng.randrange(10**11, 10**12)}"
            if id_envio not in ids_usados:
                ids_usados.add(id_envio)
                break

        admision = datetime(2026, 1, 1) + timedelta(minutes=rng.randrange(0, 270 * 24 * 60))
        ultimo = admision + timedelta(minutes=rng.randrange(6 * 60, 10 * 24 * 60))
        envio = {
            "id": id_envio,
            "en_portal": True,
            "estado": "",
            "admision": admision,
            "ultimo_movimiento": ultimo,
            "origen": rng.choice(ZONAS),
            "destino": rng.choice(ZONAS),
            "incidencias": [],
            "asociado": "",
        }

        if posicion in NO_ENCONTRADOS_FORZADOS or rng.random() < PROB_NO_ENCONTRADO:
            envio["en_portal"] = False
        elif rng.random() < PROB_SIN_ESTADO:
            envio["estado"] = ""
        else:
            nombre = rng.choices(nombres, weights=pesos)[0]
            envio["estado"] = f"{nombre} - {ultimo:%d/%m/%y %H:%M}"
            for texto in INCIDENCIAS.get(nombre, []):
                envio["incidencias"].append((ultimo - timedelta(hours=rng.randrange(1, 48)), texto))
            if nombre == "REEXPEDIDO":
                envio["asociado"] = f"7701{rng.randrange(10**11, 10**12)}"

        envios.append(envio)

    return envios


def estado_esperado(envio):
    """Lo que el scraper debería escribir en Estado_Scraping para este envío."""
    if not envio["en_portal"]:
        return "Sin registro"
    return envio["estado"] or "Sin estado"


def escribir_entrada(ruta, envios):
    """Excel de entrada del scraper: solo la columna ID_Envio, como en el original."""
    pd.DataFrame({"ID_Envio": [e["id"] for e in envios]}).to_excel(ruta, index=False)


def escribir_esperado(ruta, envios):
    with open(ruta, "w", newline="", encoding="utf-8") as f:
        escritor = csv.writer(f)
        escritor.writerow(["ID_Envio", "Estado_Esperado"])
        for e in envios:
            escritor.writerow([e["id"], estado_esperado(e)])


if __name__ == "__main__":
    envios = generar_envios()
    escribir_esperado(RAIZ / "esperado.csv", envios)
    escribir_entrada(RAIZ / "entrada_demo.xlsx", envios)
    resumen = {}
    for e in envios:
        clave = estado_esperado(e).split(" - ")[0]
        resumen[clave] = resumen.get(clave, 0) + 1
    print(f"{len(envios)} envíos generados -> esperado.csv, entrada_demo.xlsx")
    for clave, n in sorted(resumen.items(), key=lambda x: -x[1]):
        print(f"  {n:4d}  {clave}")
